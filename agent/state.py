"""LangGraph がノード間で受け渡す調査状態。

【Agent Engine 上の位置づけ】
Agent Engine の ``query()`` は内部で本状態を ``invoke`` するが、RPC の戻り値にはしない。
状態はプロセス内の作業メモリであり、SQL 失敗回数と Reflection 回数を分けて持つ。
設計メモのフィールドだけでは、SQL 構文エラーの再生成が「調査が不足」と誤認され、
Gemini/BQ の課金ループ上限が崩れるため、``sql_retry_count`` を独立させた。

【主な関数構成】
- AgentState: ノードが読み書きする TypedDict
- initial_state: query() 開始時にカウンターをゼロリセットする
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    """調査 1 件分の作業メモリ。total=False はノードが部分更新するため。

    LangGraph は返したキーだけをマージする。未初期化キーを必須にすると、
    途中ノードが KeyError で Agent Engine の query 全体を落とす。
    """

    # 入力・プランニング
    user_query: str
    plan: List[str]
    current_step: int  # 将来のステップ実行用。PoC では未使用だが状態契約を維持する。

    # 検索・データ取得コンテキスト
    retrieved_policies: List[Dict[str, Any]]
    generated_sql: Optional[str]
    sql_execution_result: Optional[List[Dict[str, Any]]]
    sql_error: Optional[str]
    sql_retry_count: int  # SQL 失敗専用。Reflection の retry_count と混ぜない。

    # 統計分析・コード実行
    analysis_code: Optional[str]
    statistical_summary: Optional[Dict[str, Any]]

    # 自己評価・制御
    critique: str
    reflection_score: int
    is_sufficient: bool
    retry_count: int  # Reflection 実行回数。上限超過で強制 Output し課金を止める。

    # 最終出力
    final_report: str
    remediation_sql: str


def initial_state(user_query: str) -> AgentState:
    """1 調査あたりのカウンターと空結果を明示的にゼロリセットする。

    LangGraph の状態は呼び出しをまたいで残らないが、キー欠落だと条件分岐が
    ``None < 2`` のような比較で落ちる。Agent Engine は例外をそのまま RPC エラーに
    するため、ここですべての制御キーを埋めておく。

    Args:
        user_query: アナリストの自然言語指示。以降の全ノードの根拠になる。

    Returns:
        空の計画・空の規程ヒット・リトライ 0 で揃えた初期 AgentState。
    """
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
