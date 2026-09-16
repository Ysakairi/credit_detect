"""ナレッジチャンクと mock RAG。アップロード資料が検索される契約を固定する。

【Agent Engine 上の位置づけ】
本番は BQ VECTOR_SEARCH だが、CI は埋め込み API を呼ばない。InMemory のトークン一致が
弱いと、mock UI で上げたマニュアルがヒットせず Path A デモが空の規程照合になる。

【主な構成】
- test_chunk_by_heading: 条項境界を残す
- test_documents_from_text_ids_stable: カテゴリ推定と ID
- test_keyword_search / test_title_query_ranks_matching_manual: 日本語クエリの順位
"""

import unittest

from agent.tools.bq_vector_search import InMemoryKnowledgeStore, chunk_text
from agent.tools.knowledge_ingest import documents_from_text


class KnowledgeTest(unittest.TestCase):
    def test_chunk_by_heading(self):
        """見出しで切らないと VECTOR_SEARCH が条番号と本文を別チャンクに引き裂く。"""
        text = "# A\nhello\n# B\nworld"
        chunks = chunk_text(text)
        self.assertGreaterEqual(len(chunks), 2)

    def test_documents_from_text_ids_stable(self):
        """PCI キーワードからカテゴリを推定し、レポートが事例と規程を混同しないようにする。"""
        docs = documents_from_text("PCI-DSS card testing\n" * 20, title="pci")
        self.assertTrue(docs)
        self.assertEqual(docs[0]["category"], "PCI_DSS")

    def test_keyword_search(self):
        """閾値と監査変数を含むクエリが本文ヒットすること。Reflection の根拠用。"""
        store = InMemoryKnowledgeStore()
        store.upsert(
            documents_from_text(
                "fraud_probability 0.85 以上は即時監視。V14 を監査する。",
                title="policy",
            )
        )
        hits = store.search("0.85 V14 即時監視")
        self.assertTrue(hits)
        self.assertIn("0.85", hits[0]["content"])

    def test_title_query_ranks_matching_manual(self):
        """タイトル一致を強くし、アップロードした正式マニュアルが汎用 SOP より上に来るようにする。"""
        store = InMemoryKnowledgeStore()
        store.upsert(documents_from_text("カードテストのBIN攻撃", title="pci_dss_card_testing"))
        store.upsert(
            documents_from_text(
                "第2条 即時監視: fraud_probability >= 0.85 かつ Amount >= 200",
                title="pol_sec_2026_004",
            )
        )
        hits = store.search("POL-SEC-2026-004 第2条の即時監視")
        self.assertTrue(hits)
        self.assertIn("pol_sec", hits[0]["title"])


if __name__ == "__main__":
    unittest.main()
