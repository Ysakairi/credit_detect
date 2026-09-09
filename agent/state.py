"""LangGraph state schema for the fraud investigation agent."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # 入力・プランニング
    user_query: str
    plan: List[str]
    current_step: int

    # 検索・データ取得コンテキスト
    retrieved_policies: List[Dict[str, Any]]
    generated_sql: Optional[str]
    sql_execution_result: Optional[List[Dict[str, Any]]]
    sql_error: Optional[str]
    sql_retry_count: int

    # 統計分析・コード実行
    analysis_code: Optional[str]
    statistical_summary: Optional[Dict[str, Any]]

    # 自己評価・制御
    critique: str
    reflection_score: int
    is_sufficient: bool
    retry_count: int

    # 最終出力
    final_report: str
    remediation_sql: str


def initial_state(user_query: str) -> AgentState:
    return {
        "user_query": user_query,
        "plan": [],
        "current_step": 0,
        "retrieved_policies": [],
        "generated_sql": None,
        "sql_execution_result": None,
        "sql_error": None,
        "sql_retry_count": 0,
        "analysis_code": None,
        "statistical_summary": None,
        "critique": "",
        "reflection_score": 0,
        "is_sufficient": False,
        "retry_count": 0,
        "final_report": "",
        "remediation_sql": "",
    }
