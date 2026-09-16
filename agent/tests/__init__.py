"""Agent テストパッケージ。GCP 無しで LangGraph 契約を固定する。

【Agent Engine 上の位置づけ】
リモートデプロイ前に、set_up/query と同じグラフ辺（SQL リトライ・DML 拒否・Reflection）を
unittest で守る。ここが無いと Agent Engine 上の課金ループやガード抜けに気づけない。
"""
