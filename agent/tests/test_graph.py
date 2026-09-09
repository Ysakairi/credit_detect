"""LangGraph routing: happy path, SQL retry, reflection retry."""

from __future__ import annotations

import json
import unittest

from agent.agent import FraudInvestigationAgent
from agent.config import AgentConfig, RuntimeDeps
from agent.graph import build_fraud_investigation_graph
from agent.state import initial_state
from agent.tools.bq_client import MockSqlRunner
from agent.tools.bq_vector_search import InMemoryKnowledgeStore
from agent.tools.knowledge_ingest import documents_from_text


def _deps(llm: ScriptedLlm, sql: MockSqlRunner | None = None) -> RuntimeDeps:
    store = InMemoryKnowledgeStore()
    store.upsert(
        documents_from_text(
            "POL-SEC-2026-004 第2条: fraud_probability>=0.85 かつ Amount>=200 は即時監視。",
            title="policy",
        )
    )
    return RuntimeDeps(
        config=AgentConfig(project_id="local-mock-project", backend="mock"),
        llm=llm,
        sql_runner=sql or MockSqlRunner(),
        knowledge=store,
    )


class GraphHappyPathTest(unittest.TestCase):
    def test_end_to_end_mock_agent(self):
        agent = FraudInvestigationAgent(
            project_id="local-mock-project",
            backend="mock",
        )
        result = agent.query("高スコア取引を調査して遮断SQLを出して")
        self.assertTrue(result["plan"])
        self.assertIn("SELECT", result["generated_sql"].upper())
        self.assertGreater(result["row_count"], 0)
        self.assertIn("エグゼクティブ", result["final_report"])
        self.assertIn("SELECT", result["remediation_sql"].upper())
        self.assertTrue(result["statistical_summary"]["strong_separators"])
        self.assertTrue(result["retrieved_policies"])

    def test_sql_retry_on_error(self):
        calls = {"sql": 0}

        def router(prompt: str) -> str:
            if "SQL を1本" in prompt or "読み取り sql" in prompt:
                calls["sql"] += 1
                if calls["sql"] == 1:
                    return "SELECT raise_error FROM t"
                return "SELECT Time, Amount, Class, fraud_probability, V14 FROM t"
            if "監査人" in prompt:
                return json.dumps(
                    {"is_sufficient": True, "score": 90, "critique": "ok"},
                    ensure_ascii=False,
                )
            if "監査レポート" in prompt:
                return "## 1. 調査エグゼクティブサマリー\nretry ok\n```sql\nSELECT 1\n```"
            return "1. extract"

        class FnLlm:
            def invoke(self, prompt: str) -> str:
                return router(prompt)

        graph = build_fraud_investigation_graph(_deps(FnLlm()))
        state = graph.invoke(initial_state("調査して"))
        self.assertIsNone(state.get("sql_error"))
        self.assertGreaterEqual(state.get("sql_retry_count") or 0, 1)
        self.assertTrue(state.get("sql_execution_result"))

    def test_reflection_retry_then_output(self):
        reflections = {"n": 0}

        class FnLlm:
            def invoke(self, prompt: str) -> str:
                if "監査人" in prompt:
                    reflections["n"] += 1
                    if reflections["n"] == 1:
                        return json.dumps(
                            {
                                "is_sufficient": False,
                                "score": 40,
                                "critique": "件数不足。閾値を下げよ",
                            },
                            ensure_ascii=False,
                        )
                    return json.dumps(
                        {"is_sufficient": True, "score": 85, "critique": "十分"},
                        ensure_ascii=False,
                    )
                if "監査レポート" in prompt:
                    return "## 1. 調査エグゼクティブサマリー\n完了\n```sql\nSELECT 1\n```"
                if "SQL を1本" in prompt or "読み取り sql" in prompt:
                    return "SELECT Time, Amount, Class, fraud_probability, V14 FROM t"
                return "1. plan"

        graph = build_fraud_investigation_graph(_deps(FnLlm()))
        state = graph.invoke(initial_state("調査して"))
        self.assertGreaterEqual(state["retry_count"], 2)
        self.assertIn("エグゼクティブ", state["final_report"])

    def test_sql_guard_blocks_dml_then_retries(self):
        class FnLlm:
            def __init__(self):
                self.n = 0

            def invoke(self, prompt: str) -> str:
                if "SQL を1本" in prompt or "読み取り sql" in prompt:
                    self.n += 1
                    if self.n == 1:
                        return "DELETE FROM t"
                    return "SELECT Amount, fraud_probability, V14, Class FROM t"
                if "監査人" in prompt:
                    return json.dumps(
                        {"is_sufficient": True, "score": 80, "critique": "ok"},
                        ensure_ascii=False,
                    )
                if "監査レポート" in prompt:
                    return "## 1. 調査エグゼクティブサマリー\nsafe\n```sql\nSELECT 1\n```"
                return "1. plan"

        graph = build_fraud_investigation_graph(_deps(FnLlm()))
        state = graph.invoke(initial_state("削除せず調査"))
        self.assertNotIn("DELETE", (state.get("generated_sql") or "").upper())
        self.assertTrue(state.get("sql_execution_result"))


if __name__ == "__main__":
    unittest.main()
