"""Vertex AI Reasoning Engine compatible wrapper around the LangGraph workflow."""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent.backends import build_deps
from agent.config import AgentConfig
from agent.graph import build_fraud_investigation_graph
from agent.state import initial_state


class FraudInvestigationAgent:
    """Deployable agent. Reasoning Engine calls set_up() once, then query()."""

    def __init__(
        self,
        project_id: str,
        location: str = "asia-northeast1",
        model_name: str = "gemini-2.0-flash",
        dataset: str = "dwh_prod",
        backend: str = "local",
        config: Optional[AgentConfig] = None,
        deps=None,
    ):
        self.project_id = project_id
        self.location = location
        self.model_name = model_name
        self.dataset = dataset
        self.backend = backend
        self.config = config or AgentConfig(
            project_id=project_id,
            location=location,
            model_name=model_name,
            dataset=dataset,
            backend=backend,
        )
        self._deps = deps
        self.workflow = None

    def set_up(self):
        deps = self._deps or build_deps(self.config)
        self.workflow = build_fraud_investigation_graph(deps)
        return self

    def query(self, user_query: str) -> Dict[str, Any]:
        if self.workflow is None:
            self.set_up()
        final_state = self.workflow.invoke(initial_state(user_query))
        return {
            "user_query": final_state.get("user_query"),
            "plan": final_state.get("plan"),
            "retrieved_policies": [
                {
                    "doc_id": p.get("doc_id"),
                    "category": p.get("category"),
                    "title": p.get("title"),
                    "distance": p.get("distance"),
                    "content": (p.get("content") or "")[:1200],
                }
                for p in (final_state.get("retrieved_policies") or [])
            ],
            "final_report": final_state.get("final_report"),
            "remediation_sql": final_state.get("remediation_sql"),
            "generated_sql": final_state.get("generated_sql"),
            "sql_error": final_state.get("sql_error"),
            "statistical_summary": final_state.get("statistical_summary"),
            "critique": final_state.get("critique"),
            "reflection_score": final_state.get("reflection_score"),
            "retry_count": final_state.get("retry_count"),
            "sql_retry_count": final_state.get("sql_retry_count"),
            "row_count": len(final_state.get("sql_execution_result") or []),
        }
