"""LangGraph 状態機械（Plan → RAG → SQL → Analyze → Reflect → Report）。

【Agent Engine 上の位置づけ】
``FraudInvestigationAgent.query()`` が invoke する唯一のワークフロー。
各ノードは Gemini / BigQuery を直接持たず ``RuntimeDeps`` 経由で呼ぶ。これにより
Agent Engine 上の本番と CI の mock が同じ辺・条件分岐を共有する。

ループ上限（SQL 再生成 2 回、Reflection 再調査 2 回）は従量課金の安全弁。
失敗しても Analyzer / Output へ進み、query() が空応答や未捕捉例外で終わらないようにする。

【主な関数構成】
- build_fraud_investigation_graph: ノードと条件辺をコンパイルする
- plan_node / rag_node / sql_gen_node / execute_sql_node / analyze_node /
  reflection_node / output_node: 調査ステップ
- check_sql_status / check_reflection_status: リトライか出力かの分岐
- build_graph_from_config: スクリプトからグラフだけ欲しいときのショートカット
"""

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
    """調査グラフをコンパイルする。deps を閉包に閉じ、ノード関数のシグネチャを State のみにする。

    LangGraph / Agent Engine はノードを ``state -> partial state`` として呼び出す。
    GCP クライアントを引数に出すと pickle とテスト注入の両方で破綻するため、
    構築時に閉包へ閉じ込める。

    Args:
        deps: LLM・SQL ランナー・ナレッジストア・設定。

    Returns:
        ``invoke(AgentState)`` 可能なコンパイル済みグラフ。
    """
    config = deps.config
    llm = deps.llm
    sql_runner = deps.sql_runner
    knowledge = deps.knowledge

    graph = StateGraph(AgentState)

    def plan_node(state: AgentState) -> Dict[str, Any]:
        """指示を番号付き手順に分解する。幻覚テーブルを後段 SQL に持ち込ませないため。

        Args:
            state: ``user_query`` を含む初期状態。

        Returns:
            手順リストと current_step=0。
        """
        text = llm.invoke(planner_prompt(state["user_query"], config))
        return {"plan": numbered_plan(text), "current_step": 0}

    def rag_node(state: AgentState) -> Dict[str, Any]:
        """SQL 生成前に規程を読む。閾値（0.85 等）をモデルの記憶ではなくコーパスから根拠づけるため。

        Args:
            state: 検索クエリとして ``user_query`` を使う。

        Returns:
            VECTOR_SEARCH / キーワード検索のヒット。ストア未設定時は空リスト。
        """
        query = state["user_query"]
        docs = knowledge.search(query, top_k=3) if knowledge is not None else []
        return {"retrieved_policies": docs}

    def sql_gen_node(state: AgentState) -> Dict[str, Any]:
        """読み取り SQL を 1 本生成する。前回エラーと Reflection 指摘を再注入して自己修正させる。

        ``is_sufficient`` が真のときに critique を捨てるのは、十分と判定されたあとに
        古い「件数不足」コメントで SQL を壊さないため。失敗リトライでは sql_error を消して
        次の execute が stale error で即失敗しないようにする。

        Args:
            state: 計画、前回 SQL エラー、必要なら critique。

        Returns:
            抽出済み SQL とクリアした sql_error。
        """
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
        """ガード後の SELECT を実行する。例外を握り、Agent Engine の query 全体を落とさない。

        SqlGuardError も BQ エラーも同じリトライ経路に乗せる。DML を例外で止めつつ、
        グラフを Output まで進めればアナリストに「拒否した理由」をレポートできる。

        Args:
            state: ``generated_sql``。

        Returns:
            成功時は行データ、失敗時は sql_error とインクリメント済み sql_retry_count。
        """
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
        """LLM 生成 Python を exec せず、SOP カーネルだけを走らせる。

        Agent Engine を汎用コード実行サンドボックスにしないため。監査変数の KS/IV/Cliff
        をレポートと Reflection の共通言語にする。

        Args:
            state: SQL 結果行。失敗後は空でもよい。

        Returns:
            統計サマリーと、実行したカーネル名（任意コードは持たない）。
        """
        records = state.get("sql_execution_result") or []
        summary = run_statistical_analysis(records)
        return {
            "statistical_summary": summary,
            "analysis_code": "run_statistical_analysis(records)  # SOP-MLOPS-2026-002 kernel",
        }

    def reflection_node(state: AgentState) -> Dict[str, Any]:
        """証拠の十分性を自己採点する。LLM の is_sufficient より数値スコア 80 を優先する。

        モデルは「十分」と書きつつ score=60 を返すことがある。黙って成功扱いにすると
        再調査が走らず、薄いレポートが監査成果になる。retry_count は成功時も増やし、
        UI が何回批判したかを残し、上限判定を単純な整数比較にする。

        Args:
            state: 規程・件数・統計・生成 SQL。

        Returns:
            is_sufficient, reflection_score, critique, retry_count。
        """
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
        """監査 Markdown と監視用 SELECT を確定する。PoC は是正 DML を実行しない契約。

        フェンス抽出は「レポート本文に紛れた SELECT」を UI の是正タブへ分離するため。
        抽出失敗時は空文字にし、本文側の提案は残す。

        Args:
            state: 規程・統計・critique・生成 SQL・件数。

        Returns:
            final_report と remediation_sql。
        """
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
        """SQL 失敗時だけ生成へ戻す。上限後は分析へ進め、query() を必ず完了させる。

        Args:
            state: sql_error と sql_retry_count。

        Returns:
            ``retry_sql`` または ``proceed_analysis``。
        """
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
        """不足かつ予算内なら SQL から組み直す。無限 Reflection で Gemini+BQ を回さないため。

        retry_count は reflection_node で先に +1 済み。max=2 なら「評価 2 回・再調査 1 回」。

        Args:
            state: is_sufficient と retry_count。

        Returns:
            ``retry_investigation`` または ``generate_output``。
        """
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
    """設定または注入 deps からグラフだけを得る。Agent ラッパを経由しないローカル検証用。

    Args:
        project_id: GCP プロジェクト。
        location: リージョン。
        model_name: Gemini モデル。
        deps: 省略時は backend=local 相当の依存を組み立てる。

    Returns:
        コンパイル済み LangGraph。
    """
    if deps is None:
        from agent.backends import build_deps

        deps = build_deps(
            AgentConfig(project_id=project_id, location=location, model_name=model_name)
        )
    return build_fraud_investigation_graph(deps)
