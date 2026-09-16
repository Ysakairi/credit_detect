"""LLM の自由テキストから SQL / JSON / 手順リストを取り出す。

【Agent Engine 上の位置づけ】
Gemini に structured output を強制しない。Agent Engine のモデル世代差や Flash 系の
フェンス崩れでグラフ全体を落とさないため、パース失敗を例外ではなく弱い結果にする。
Reflection は JSON 化に失敗しても「十分」とみなさず、critique に原文を残す。

【主な関数構成】
- extract_sql: コードフェンスまたは先頭 SELECT/WITH から 1 文を抜く
- extract_json: フェンス内または最初のオブジェクトを dict にする
- parse_reflection: 採点 JSON を (十分フラグ, スコア, 指摘) に正規化する
- numbered_plan: Planner の番号付き出力を手順リストにする
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple


_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def extract_sql(text: str) -> str:
    """レポートや説明文に埋まった SQL だけを実行・是正タブへ渡す。

    末尾 `;` を落とすのは、sanitize_sql がセミコロンを文区切りとみなして中身を壊す前に
    単一文へ正規化するため。フェンスが無いときは全文を返し、SQL Gen の生出力にも耐える。

    Args:
        text: LLM 生出力。

    Returns:
        抽出した SQL 文字列。空入力は空文字。
    """
    if not text:
        return ""
    fenced = _SQL_FENCE.findall(text)
    if fenced:
        return fenced[0].strip().rstrip(";")
    stripped = text.strip()
    if stripped.lower().startswith(("select", "with")):
        return stripped.strip().rstrip(";")
    return stripped.replace("```sql", "").replace("```", "").strip().rstrip(";")


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Reflection が前置き文付きで JSON を返しても、グラフを落とさず dict を拾う。

    末尾カンマ除去は Flash 系のよくある不正 JSON 対策。修復できないときは None を返し、
    呼び出し側が「不十分」へ倒せるようにする。

    Args:
        text: LLM 生出力。

    Returns:
        オブジェクト dict。失敗時は None。配列 JSON は使わないので破棄する。
    """
    if not text:
        return None
    candidates = _JSON_FENCE.findall(text)
    raw = candidates[-1] if candidates else text
    match = _JSON_OBJECT.search(raw)
    if not match:
        return None
    blob = match.group(0)
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        blob = blob.replace("true", "true").replace("false", "false")
        blob = re.sub(r",\s*}", "}", blob)
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def parse_reflection(text: str) -> Tuple[bool, int, str]:
    """採点をグラフの条件辺が読める三値にする。パース失敗を成功扱いにしない。

    is_sufficient 欠落時は score>=80 で補う。逆に score 不正は 0 にし、再調査または
    「根拠薄弱」レポートへ倒す。critique が空なら原文を残し、監査人が生出力を追えるようにする。

    Args:
        text: Reflection ノードの LLM 出力。

    Returns:
        (is_sufficient, score, critique)。score は 0 以上の int。
    """
    parsed = extract_json(text) or {}
    score = parsed.get("score", parsed.get("reflection_score", 0))
    try:
        score_i = int(score)
    except (TypeError, ValueError):
        score_i = 0
    if "is_sufficient" in parsed:
        is_sufficient = bool(parsed.get("is_sufficient"))
    else:
        is_sufficient = score_i >= 80
    critique = str(parsed.get("critique") or text or "").strip()
    return is_sufficient, score_i, critique


def numbered_plan(text: str) -> list[str]:
    """番号や箇条書きを剥がし、SQL Gen が手順を条件に翻訳しやすくする。

    空なら全文を 1 ステップにする。Planner が「1.」を付け忘れてても計画キーが空にならない。

    Args:
        text: Planner 出力。

    Returns:
        空行を除いた手順文字列のリスト。入力が空なら空リスト。
    """
    steps = []
    for line in (text or "").splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
        if cleaned:
            steps.append(cleaned)
    return steps or [text.strip()] if text and text.strip() else []
