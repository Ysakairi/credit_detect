"""LLM アダプタ。本番は Vertex Gemini、検証はスクリプト応答。

【Agent Engine 上の位置づけ】
LangGraph の Planner / SQL Gen / Reflection / Output はすべて ``LlmClient.invoke`` 経由。
``ChatVertexAI`` をノードに直接置くと pickle できず Agent Engine デプロイが失敗するため、
遅延初期化の薄いラッパにする。temperature を低くし、調査 SQL と JSON 採点の揺らぎを抑える。

ScriptedLlm は Agent Engine には載せない。GCP なしでグラフ契約（リトライ分岐を含む）を
CI で固定するためだけに存在する。

【主な関数構成】
- LlmClient: ノードが依存する Protocol
- VertexLlm: Agent Engine / Cloud Run の本番 LLM
- ScriptedLlm: キーワードで既定応答を返すテスト用 LLM
- default_scripted_response: mock バックエンドのシナリオ本文
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Dict, List, Optional, Protocol


class LlmClient(Protocol):
    """LangGraph ノードが要求する最小インターフェース。

    Args 相当: invoke(prompt) の prompt は各ノードの完成済み指示文。
    Returns 相当: モデルの生テキスト。構造化は parsing 側で行う。
    """

    def invoke(self, prompt: str) -> str:
        ...


class VertexLlm:
    """ChatVertexAI を遅延生成し、Agent Engine の pickle 対象から GCP クライアントを外す。"""

    def __init__(self, project_id: str, location: str, model_name: str, temperature: float = 0.1):
        """接続情報だけ保持する。モデルオブジェクトは作らない。

        Args:
            project_id: Vertex AI プロジェクト。
            location: モデルリージョン。データセット所在地と揃える。
            model_name: 例: gemini-2.0-flash。
            temperature: 低めにして Text-to-SQL の再現性を優先する。
        """
        self.project_id = project_id
        self.location = location
        self.model_name = model_name
        self.temperature = temperature
        self._model = None

    def _chat(self):
        """最初の invoke まで SDK import を遅らせ、セットアップ時の認証失敗を query まで延ばす。

        Returns:
            初期化済み ChatVertexAI。
        """
        if self._model is None:
            from langchain_google_vertexai import ChatVertexAI

            self._model = ChatVertexAI(
                model_name=self.model_name,
                project=self.project_id,
                location=self.location,
                temperature=self.temperature,
            )
        return self._model

    def invoke(self, prompt: str) -> str:
        """Gemini 応答を常に str に正規化する。

        Agent Engine 上で multipart content（list）が返ると、後段の SQL/JSON 抽出が
        型エラーで調査全体を落とすため、ここで連結する。

        Args:
            prompt: ノードが組み立てた完成プロンプト。

        Returns:
            モデル出力テキスト。
        """
        response = self._chat().invoke(prompt)
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return str(content)


class ScriptedLlm:
    """プロンプト中のキーワードで固定応答を返す。CI が Gemini 課金と非決定性から独立するため。"""

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        """上書き辞書を受け、特定ノードだけ失敗させるテストを可能にする。

        Args:
            responses: 部分文字列 → 応答（または prompt を受ける callable）。
        """
        self.responses = responses or {}
        self.calls: List[str] = []

    def invoke(self, prompt: str) -> str:
        """カスタム応答が無ければ default_scripted_response に倒し、E2E mock を成立させる。

        Args:
            prompt: ノードからの完成プロンプト。

        Returns:
            対応する固定テキスト。
        """
        self.calls.append(prompt)
        for key, value in self.responses.items():
            if key.lower() in prompt.lower():
                return value if not isinstance(value, Callable) else value(prompt)
        return default_scripted_response(prompt)


def default_scripted_response(prompt: str) -> str:
    """mock が Planner → SQL → Reflection → Output の順に破綻しないための既定シナリオ。

    キーワード判定はプロンプト側の役割名に依存する。文言を変えるときは本関数と
    test_graph の両方を直す必要がある。

    Args:
        prompt: ノードプロンプト全文。

    Returns:
        そのノード種別向けの固定出力。
    """
    lower = prompt.lower()
    if "シニアセキュリティ監査人" in prompt or '"is_sufficient"' in lower:
        return json.dumps(
            {
                "is_sufficient": True,
                "score": 88,
                "critique": "件数と SOP 指標（KS/Cliff）が揃っており、規程の 0.85 閾値とも整合している。",
            },
            ensure_ascii=False,
        )
    if "監査レポート" in prompt or "エグゼクティブサマリー" in prompt:
        return """## 1. 調査エグゼクティブサマリー
高スコアかつ高額帯に不正クラスが集中しています。V14/V17 が強い分離変数です。

## 2. 検出された不正パターンの統計分析（特徴量 V1〜V28 の傾向）
監査変数 V14, V17, V12 を SOP 第7章のカーネルで評価しました。

## 3. 規程（ポリシー）照合結果
PCI-DSS / 社内規程の高額・高スコア即時監視条項に該当します。

## 4. 推奨される即時是正アクション
スコア 0.85 以上かつ Amount>=200 を監視ビューに登録してください。

```sql
SELECT Time, Amount, fraud_probability, predicted_Class
FROM `local-mock-project.dwh_prod.ulb_fraud_detection_predictions`
WHERE fraud_probability >= 0.85 AND Amount >= 200
```
"""
    if "text-to-sql" in lower or "sql を1本" in prompt or "読み取り sql" in prompt:
        if "raise_error" in lower:
            return "SELECT raise_error FROM nowhere"
        if "エラー" in prompt or "unrecognized" in lower:
            return """```sql
SELECT Time, Amount, Class, predicted_Class, fraud_probability, V12, V14, V17
FROM `local-mock-project.dwh_prod.ulb_fraud_detection_predictions`
WHERE fraud_probability >= 0.85
ORDER BY fraud_probability DESC
```"""
        return """```sql
SELECT Time, Amount, Class, predicted_Class, fraud_probability, V12, V14, V17
FROM `local-mock-project.dwh_prod.ulb_fraud_detection_predictions`
WHERE fraud_probability >= 0.85 AND Amount >= 200
ORDER BY fraud_probability DESC
```"""
    return """1. 日次推論テーブルから fraud_probability >= 0.85 かつ Amount >= 200 を抽出する
2. PCI-DSS と社内規程の高額取引条項を照合する
3. V14/V17/V12 の KS と Cliff's Delta を算出する
"""
