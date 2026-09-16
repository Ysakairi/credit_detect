"""Vertex AI Agent Engine にデプロイする不正調査エージェントのパッケージ入口。

【Agent Engine 上の位置づけ】
Agent Engine（旧 Reasoning Engine）は、デプロイ時にこのパッケージを extra_packages
として同梱し、公開クラス ``FraudInvestigationAgent`` だけを pickle してリモート実行する。
呼び出し側が ``from agent import FraudInvestigationAgent`` できるように、ここから
再エクスポートする。内部の LangGraph ノードや BigQuery クライアントは pickle 対象に
しない（``set_up()`` で再構築するため）。

【主な構成】
- FraudInvestigationAgent: Agent Engine 契約（set_up / query）を満たす唯一の公開クラス
"""

from agent.agent import FraudInvestigationAgent

__all__ = ["FraudInvestigationAgent"]
