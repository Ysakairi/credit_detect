"""LangGraph state machine: plan → RAG → SQL → analyze → reflect → report."""

from __future__ import annotations

import logging
from typing import Any, Dict, Literal

from langgraph.graph import END, StateGraph

from agent.config import AgentConfig, RuntimeDeps
from agent.parsing import extract_sql, numbered_plan, parse_reflection
from agent.prompts import output_prompt, planner_prompt, reflection_prompt, sql_gen_prompt
from agent.sql_guard import SqlGuardError
from agent.state import AgentState
from agent.tools.data_analyzer import run_statistical_analysis

logger = logging.getLogger(__name__)


def build_fraud_investigation_graph(deps: RuntimeDeps):
    config = deps.config
    llm = deps.llm
    sql_runner = deps.sql_runner
    knowledge = deps.knowledge

    graph = StateGraph(AgentState)

    def plan_node(state: AgentState) -> Dict[str, Any]:
        text = llm.invoke(planner_prompt(state["user_query"], config))
        return {"plan": numbered_plan(text), "current_step": 0}

    def rag_node(state: AgentState) -> Dict[str, Any]:
        query = state["user_query"]
        docs = knowledge.search(query, top_k=3) if knowledge is not None else []
        return {"retrieved_policies": docs}

    def sql_gen_node(state: AgentState) -> Dict[str, Any]:
        text = llm.invoke(
            sql_gen_prompt(
                state["user_query"],
                state.get("plan") or [],
                config,
                sql_error=state.get("sql_error"),
                critique=None if state.get("is_sufficient") else state.get("critique"),
            )
        )
        return {"generated_sql": extract_sql(text), "sql_error": None}

    def execute_sql_node(state: AgentState) -> Dict[str, Any]:
        sql = state.get("generated_sql") or ""
        try:
            records = sql_runner.execute(sql)
            return {
                "sql_execution_result": records,
                "sql_error": None,
            }
        except (SqlGuardError, Exception) as exc:
            logger.warning("SQL execution failed: %s", exc)
            return {
                "sql_error": str(exc),
                "sql_retry_count": int(state.get("sql_retry_count") or 0) + 1,
            }

    def analyze_node(state: AgentState) -> Dict[str, Any]:
        records = state.get("sql_execution_result") or []
        summary = run_statistical_analysis(records)
        return {
            "statistical_summary": summary,
            "analysis_code": "run_statistical_analysis(records)  # SOP-MLOPS-2026-002 kernel",
        }

    def reflection_node(state: AgentState) -> Dict[str, Any]:
        records = state.get("sql_execution_result") or []
        text = llm.invoke(
            reflection_prompt(
                state["user_query"],
                state.get("retrieved_policies") or [],
                len(records),
                state.get("statistical_summary"),
                state.get("generated_sql"),
            )
        )
        is_sufficient, score, critique = parse_reflection(text)
        retry_count = int(state.get("retry_count") or 0) + 1
        if score and score < 80:
            is_sufficient = False
        return {
            "is_sufficient": is_sufficient,
            "reflection_score": score,
            "critique": critique,
            "retry_count": retry_count,
        }

    def output_node(state: AgentState) -> Dict[str, Any]:
        records = state.get("sql_execution_result") or []
        text = llm.invoke(
            output_prompt(
                state["user_query"],
                state.get("retrieved_policies") or [],
                state.get("statistical_summary"),
                state.get("critique") or "",
                state.get("generated_sql"),
                len(records),
                config,
            )
        )
        remediation = extract_sql(text) if "```sql" in text or "SELECT" in text.upper() else ""
        return {"final_report": text, "remediation_sql": remediation}

    graph.add_node("planner", plan_node)
    graph.add_node("rag", rag_node)
    graph.add_node("sql_gen", sql_gen_node)
    graph.add_node("sql_exec", execute_sql_node)
    graph.add_node("analyzer", analyze_node)
    graph.add_node("reflector", reflection_node)
    graph.add_node("generator", output_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "rag")
    graph.add_edge("rag", "sql_gen")
    graph.add_edge("sql_gen", "sql_exec")

    def check_sql_status(state: AgentState) -> Literal["retry_sql", "proceed_analysis"]:
        if state.get("sql_error") and int(state.get("sql_retry_count") or 0) < config.max_sql_retries:
            return "retry_sql"
        return "proceed_analysis"

    graph.add_conditional_edges(
        "sql_exec",
        check_sql_status,
        {"retry_sql": "sql_gen", "proceed_analysis": "analyzer"},
    )
    graph.add_edge("analyzer", "reflector")

    def check_reflection_status(
        state: AgentState,
    ) -> Literal["retry_investigation", "generate_output"]:
        if (
            not state.get("is_sufficient")
            and int(state.get("retry_count") or 0) < config.max_reflection_retries
        ):
            return "retry_investigation"
        return "generate_output"

    graph.add_conditional_edges(
        "reflector",
        check_reflection_status,
        {"retry_investigation": "sql_gen", "generate_output": "generator"},
    )
    graph.add_edge("generator", END)
    return graph.compile()


def build_graph_from_config(
    project_id: str,
    location: str = "asia-northeast1",
    model_name: str = "gemini-2.0-flash",
    deps: RuntimeDeps | None = None,
):
    if deps is None:
        from agent.backends import build_deps

        deps = build_deps(
            AgentConfig(project_id=project_id, location=location, model_name=model_name)
        )
    return build_fraud_investigation_graph(deps)
