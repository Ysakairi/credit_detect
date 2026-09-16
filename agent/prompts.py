"""LangGraph 各ノードへ渡す指示文テンプレート。

【Agent Engine 上の位置づけ】
Gemini への唯一の「役割定義」置き場。ノード実装（graph.py）にプロンプトを散らすと、
Agent Engine 上で SQL 幻覚や採点基準のずれが起きたときに修正箇所が分からなくなる。
スキーマカタログは毎回 ``schema_prompt`` で注入する。モデルにテーブル名を記憶させない。

【主な関数構成】
- planner_prompt: 指示を 3 系統（データ / 規程 / 統計）に分解させる
- sql_gen_prompt: 読み取り SQL 1 本。エラーと critique を再注入する
- reflection_prompt: JSON 採点。捏造禁止と SOP 指標を固定する
- output_prompt: 監査 4 見出しと是正 SELECT を強制する
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.config import AgentConfig, schema_prompt


PLANNER_SYSTEM = """あなたはクレジットカード不正対策のリード調査官です。
ユーザー指示を、BigQuery から取るデータ・参照すべき規程・統計で検証する仮説、の3系統に分解します。
推測でテーブル名を作らず、与えられた credit_detect スキーマだけを使います。
出力は番号付きの短い手順のみ。解説文は不要です。"""


def planner_prompt(user_query: str, config: AgentConfig) -> str:
    """計画を短く固定し、後段 SQL が「存在しないテーブル」を引き継がないようにする。

    Args:
        user_query: アナリスト指示。
        config: 実テーブル FQDN を schema_prompt 経由で埋め込むため。

    Returns:
        Planner ノードへ渡す完成プロンプト。
    """
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
    """失敗理由を同じコンテキストに載せ、Agent Engine 上の SQL リトライを意味ある修正にする。

    複数ステートメント禁止は sql_guard と二重化している。モデルが `;` で DML を
    連結してくる事例を、ガード到達前に減らすため。

    Args:
        user_query: 元の調査指示。
        plan: Planner の手順。抽出条件の根拠にする。
        config: スキーマと LIMIT 上限の注入元。
        sql_error: 直前のガード/BQ エラー。初回は None。
        critique: Reflection が要求した追加条件。十分判定後は呼び出し側が None にする。

    Returns:
        SQL Gen ノードへ渡す完成プロンプト。
    """
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
    """監査人ロールで自己採点させる。規程と SOP 指標を入力に含め、空論の「十分」を防ぐ。

    JSON のみを要求するのは parse_reflection がテキストからオブジェクトを拾うため。
    score<80 なら次 SQL 条件を critique に書かせ、リトライを具体的にする。

    Args:
        user_query: 元指示。答えているかの判定基準。
        policies: RAG ヒット。閾値条項の引用元。
        row_count: SQL 件数。0 件を十分と誤認させないため。
        statistical_summary: Analyzer の KS/IV/Cliff。
        generated_sql: どの抽出条件だったかの再現用。

    Returns:
        Reflection ノードへ渡す完成プロンプト。
    """
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
    """監査 4 見出しを固定し、レポート品質をモデルの自由記述に委ねない。

    是正は SELECT 提案まで。Agent Engine から BQ へ DML を出させない契約をプロンプトでも再掲する。
    存在しない指標の捏造禁止は、統計サマリーが空（SQL 失敗）のときに特に効く。

    Args:
        user_query: 元指示。
        policies: 照合結果に引用する規程抜粋。
        statistical_summary: 第2章の根拠。
        critique: 限界の正直な記載用。
        generated_sql: 再現用。
        row_count: サマリーの規模感。
        config: 是正 SQL の対象テーブル FQDN。

    Returns:
        Output ノードへ渡す完成プロンプト。
    """
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
