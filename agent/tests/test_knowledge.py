"""Knowledge chunking and in-memory search."""

import unittest

from agent.tools.bq_vector_search import InMemoryKnowledgeStore, chunk_text
from agent.tools.knowledge_ingest import documents_from_text


class KnowledgeTest(unittest.TestCase):
    def test_chunk_by_heading(self):
        text = "# A\nhello\n# B\nworld"
        chunks = chunk_text(text)
        self.assertGreaterEqual(len(chunks), 2)

    def test_documents_from_text_ids_stable(self):
        docs = documents_from_text("PCI-DSS card testing\n" * 20, title="pci")
        self.assertTrue(docs)
        self.assertEqual(docs[0]["category"], "PCI_DSS")

    def test_keyword_search(self):
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


if __name__ == "__main__":
    unittest.main()
