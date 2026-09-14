"""Prompt templates for each LangGraph node."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.config import AgentConfig, schema_prompt


PLANNER_SYSTEM = """あなたはクレジットカード不正対策のリード調査官です。
ユーザー指示を、BigQuery から取るデータ・参照すべき規程・統計で検証する仮説、の3系統に分解します。
推測でテーブル名を作らず、与えられた credit_detect スキーマだけを使います。
出力は番号付きの短い手順のみ。解説文は不要です。"""


def planner_prompt(user_query: str, config: AgentConfig) -> str:
    return f"""{PLANNER_SYSTEM}

{schema_prompt(config)}

ユーザー指示:
{user_query}

出力例:
1. `...predictions` から fraud_probability >= 0.85 かつ Amount >= 200 を抽出する
2. PCI-DSS / 社内規程の高額・連続試行条項を照合する
3. V14/V17/V12 の KS と Cliff's Delta を計算する
"""


def sql_gen_prompt(
    user_query: str,
    plan: List[str],
    config: AgentConfig,
    sql_error: Optional[str] = None,
    critique: Optional[str] = None,
) -> str:
    error_block = ""
    if sql_error:
        error_block = f"\n【前回の SQL エラー。標準SQLとして修正すること】\n{sql_error}\n"
    critique_block = ""
    if critique:
        critique_block = f"\n【Reflection からの不足指摘】\n{critique}\n"
    return f"""あなたは BigQuery の Text-to-SQL 担当です。調査用の読み取り SQL を1本だけ書いてください。

{schema_prompt(config)}

調査計画:
{chr(10).join(plan)}

ユーザー指示:
{user_query}
{error_block}{critique_block}

【出力】
SQL のみ（```sql フェンス可）。複数ステートメント禁止。
分析に必要な列（Time, Amount, Class, predicted_Class, fraud_probability, V1〜V28 のうち関連列）を含めること。
"""


def reflection_prompt(
    user_query: str,
    policies: List[Dict[str, Any]],
    row_count: int,
    statistical_summary: Optional[Dict[str, Any]],
    generated_sql: Optional[str],
) -> str:
    policy_titles = [
        f"- {p.get('title') or p.get('doc_id')}: {(p.get('content') or '')[:400]}"
        for p in policies
    ]
    return f"""あなたはシニアセキュリティ監査人です。調査結果を批判的に採点してください。

【指示】{user_query}
【SQL】{generated_sql}
【件数】{row_count}
【参照規程】
{chr(10).join(policy_titles) or '(規程ヒットなし)'}
【統計】{statistical_summary}

【評価基準】
1. 質問に答える十分な件数・期間・閾値か
2. SOP-MLOPS-2026-002（KS / IV / Cliff's Delta。Zスコア差は参考値）に沿っているか
3. 規程の閾値（fraud_probability>=0.85, PSI, PR-AUC）と矛盾していないか
4. データに無い数値を捏造していないか

JSON のみ:
{{"is_sufficient": true, "score": 0, "critique": "日本語の理由"}}

score は 0-100。80 未満なら is_sufficient を false にし、次に投げる SQL 条件を critique に書く。
"""


def output_prompt(
    user_query: str,
    policies: List[Dict[str, Any]],
    statistical_summary: Optional[Dict[str, Any]],
    critique: str,
    generated_sql: Optional[str],
    row_count: int,
    config: AgentConfig,
) -> str:
    policy_excerpt = "\n\n".join(
        f"### {p.get('title')}\n{(p.get('content') or '')[:800]}" for p in policies
    )
    return f"""不正調査の監査レポートと、監視用の是正 SQL を書いてください。
事実は統計サマリーと規程引用に限定し、存在しない指標値は作らないでください。

ユーザー質問: {user_query}
件数: {row_count}
実行SQL: {generated_sql}
自己レビュー: {critique}
統計サマリー: {statistical_summary}

参照規程:
{policy_excerpt or '(なし)'}

必須見出し:
## 1. 調査エグゼクティブサマリー
## 2. 検出された不正パターンの統計分析（特徴量 V1〜V28 の傾向）
## 3. 規程（ポリシー）照合結果
## 4. 推奨される即時是正アクション

最後に BigQuery で監視ビューにできる SELECT を ```sql フェンスで1本。
対象は `{config.predictions_table}`。DML は出さない（PoC は提案まで）。
"""
