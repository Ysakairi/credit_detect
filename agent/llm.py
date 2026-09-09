"""LLM adapters: Vertex Gemini and a scripted mock for tests / offline UI."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Dict, List, Optional, Protocol


class LlmClient(Protocol):
    def invoke(self, prompt: str) -> str:
        ...


class VertexLlm:
    def __init__(self, project_id: str, location: str, model_name: str, temperature: float = 0.1):
        self.project_id = project_id
        self.location = location
        self.model_name = model_name
        self.temperature = temperature
        self._model = None

    def _chat(self):
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
        response = self._chat().invoke(prompt)
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return str(content)


class ScriptedLlm:
    """Returns canned completions selected by prompt keywords."""

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        self.responses = responses or {}
        self.calls: List[str] = []

    def invoke(self, prompt: str) -> str:
        self.calls.append(prompt)
        for key, value in self.responses.items():
            if key.lower() in prompt.lower():
                return value if not isinstance(value, Callable) else value(prompt)
        return default_scripted_response(prompt)


def default_scripted_response(prompt: str) -> str:
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
