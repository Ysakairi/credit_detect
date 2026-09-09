"""Hybrid RAG over investigation manuals using BigQuery Vector Search.

Embeddings are produced with Vertex AI `text-embedding-004` (768-d) and stored
in `dwh_prod.fraud_investigation_knowledge`. Query-time search also embeds via
the same API so a BigQuery remote model / Cloud Resource Connection is optional
for the PoC.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Protocol, Sequence

from agent.config import (
    EMBEDDING_DIMENSIONS,
    VECTOR_TOP_K,
    AgentConfig,
)

logger = logging.getLogger(__name__)


class KnowledgeStore(Protocol):
    def search(self, query: str, top_k: int = VECTOR_TOP_K) -> List[Dict[str, Any]]:
        ...

    def upsert(self, documents: Sequence[Dict[str, Any]]) -> int:
        ...


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> List[str]:
    """Split uploaded manuals into embedding-sized passages."""
    cleaned = re.sub(r"\r\n?", "\n", text or "").strip()
    if not cleaned:
        return []
    parts = re.split(r"\n(?=#{1,3} )", cleaned)
    chunks: List[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= chunk_size:
            chunks.append(part)
            continue
        start = 0
        while start < len(part):
            end = min(len(part), start + chunk_size)
            chunks.append(part[start:end].strip())
            if end >= len(part):
                break
            start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(a[i]) ** 2 for i in range(n))) or 1e-12
    nb = math.sqrt(sum(float(b[i]) ** 2 for i in range(n))) or 1e-12
    return dot / (na * nb)


class InMemoryKnowledgeStore:
    """Local cosine search used by mock backend and unit tests."""

    def __init__(self, documents: Optional[List[Dict[str, Any]]] = None):
        self.documents: List[Dict[str, Any]] = list(documents or [])

    def upsert(self, documents: Sequence[Dict[str, Any]]) -> int:
        added = 0
        existing = {d.get("doc_id") for d in self.documents}
        for doc in documents:
            if doc.get("doc_id") in existing:
                self.documents = [d for d in self.documents if d.get("doc_id") != doc.get("doc_id")]
            self.documents.append(dict(doc))
            added += 1
        return added

    def search(self, query: str, top_k: int = VECTOR_TOP_K) -> List[Dict[str, Any]]:
        scored: List[Dict[str, Any]] = []
        q_emb = None
        for doc in self.documents:
            if doc.get("embedding"):
                continue
        # Prefer embeddings when present; otherwise token overlap.
        query_tokens = set(re.findall(r"[A-Za-z0-9_]+|[\u3040-\u30ff\u4e00-\u9fff]+", query.lower()))
        for doc in self.documents:
            item = dict(doc)
            emb = doc.get("embedding")
            if q_emb is not None and emb:
                item["distance"] = 1.0 - _cosine(q_emb, emb)
            else:
                text = f"{doc.get('title', '')} {doc.get('content', '')}".lower()
                overlap = sum(1 for tok in query_tokens if tok and tok in text)
                item["distance"] = 1.0 / (1.0 + overlap)
            scored.append(item)
        scored.sort(key=lambda d: d.get("distance", 1.0))
        return scored[:top_k]


class BigQueryKnowledgeStore:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._bq = None
        self._embedding_model = None

    def _bq_client(self):
        if self._bq is None:
            from google.cloud import bigquery

            self._bq = bigquery.Client(
                project=self.config.project_id, location=self.config.location
            )
        return self._bq

    def ensure_table(self) -> None:
        from google.cloud import bigquery

        client = self._bq_client()
        table_id = self.config.knowledge_table
        schema = [
            bigquery.SchemaField("doc_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("category", "STRING"),
            bigquery.SchemaField("title", "STRING"),
            bigquery.SchemaField("content", "STRING"),
            bigquery.SchemaField("source_uri", "STRING"),
            bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
        ]
        table = bigquery.Table(table_id, schema=schema)
        table.description = "Fraud investigation manuals for BigQuery Vector Search"
        client.create_table(table, exists_ok=True)

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        try:
            from vertexai.language_models import TextEmbeddingModel

            if self._embedding_model is None:
                import vertexai

                vertexai.init(project=self.config.project_id, location=self.config.location)
                self._embedding_model = TextEmbeddingModel.from_pretrained(
                    self.config.embedding_model
                )
            vectors = []
            batch_size = 16
            for i in range(0, len(texts), batch_size):
                batch = list(texts[i : i + batch_size])
                embs = self._embedding_model.get_embeddings(batch)
                for emb in embs:
                    values = list(emb.values)
                    if len(values) < EMBEDDING_DIMENSIONS:
                        values.extend([0.0] * (EMBEDDING_DIMENSIONS - len(values)))
                    vectors.append(values[:EMBEDDING_DIMENSIONS])
            return vectors
        except Exception as exc:  # pragma: no cover - environment specific
            logger.warning("Vertex embedding failed (%s); storing empty vectors", exc)
            return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]

    def upsert(self, documents: Sequence[Dict[str, Any]]) -> int:
        if not documents:
            return 0
        self.ensure_table()
        need_embed = [d for d in documents if not d.get("embedding")]
        if need_embed:
            vectors = self.embed_texts([str(d.get("content") or "") for d in need_embed])
            for doc, vec in zip(need_embed, vectors):
                doc["embedding"] = vec
        from google.cloud import bigquery

        client = self._bq_client()
        ids = [str(d["doc_id"]) for d in documents]
        # Replace by doc_id so re-seeding is idempotent.
        placeholders = ", ".join(f"@id{i}" for i in range(len(ids)))
        params = [
            bigquery.ScalarQueryParameter(f"id{i}", "STRING", doc_id)
            for i, doc_id in enumerate(ids)
        ]
        delete_sql = (
            f"DELETE FROM `{self.config.knowledge_table}` WHERE doc_id IN ({placeholders})"
        )
        client.query(
            delete_sql, job_config=bigquery.QueryJobConfig(query_parameters=params)
        ).result()
        rows = []
        for doc in documents:
            rows.append(
                {
                    "doc_id": str(doc["doc_id"]),
                    "category": doc.get("category") or "UPLOADED",
                    "title": doc.get("title") or "",
                    "content": doc.get("content") or "",
                    "source_uri": doc.get("source_uri") or "",
                    "embedding": list(doc.get("embedding") or []),
                }
            )
        errors = client.insert_rows_json(self.config.knowledge_table, rows)
        if errors:
            raise RuntimeError(f"knowledge insert errors: {errors}")
        return len(rows)

    def search(self, query: str, top_k: int = VECTOR_TOP_K) -> List[Dict[str, Any]]:
        self.ensure_table()
        vectors = self.embed_texts([query])
        query_embedding = vectors[0] if vectors else []
        if query_embedding and any(query_embedding):
            return self._vector_search(query_embedding, top_k)
        return self._keyword_search(query, top_k)

    def _vector_search(self, embedding: List[float], top_k: int) -> List[Dict[str, Any]]:
        from google.cloud import bigquery

        sql = f"""
        SELECT
          base.doc_id,
          base.category,
          base.title,
          base.content,
          base.source_uri,
          distance
        FROM VECTOR_SEARCH(
          TABLE `{self.config.knowledge_table}`,
          'embedding',
          (SELECT @query_embedding AS embedding),
          top_k => @top_k,
          distance_type => 'COSINE'
        )
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("query_embedding", "FLOAT64", embedding),
                bigquery.ScalarQueryParameter("top_k", "INT64", int(top_k)),
            ]
        )
        try:
            result = self._bq_client().query(sql, job_config=job_config).result()
            return [dict(row.items()) for row in result]
        except Exception as exc:
            logger.warning("VECTOR_SEARCH failed (%s); falling back to keyword search", exc)
            return self._keyword_search(" ".join(str(x) for x in embedding[:3]), top_k)

    def _keyword_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        from google.cloud import bigquery

        sql = f"""
        SELECT doc_id, category, title, content, source_uri,
               1.0 AS distance
        FROM `{self.config.knowledge_table}`
        WHERE LOWER(CONCAT(IFNULL(title,''), ' ', IFNULL(content,'')))
              LIKE CONCAT('%', LOWER(@q), '%')
        LIMIT @top_k
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("q", "STRING", query[:200]),
                bigquery.ScalarQueryParameter("top_k", "INT64", int(top_k)),
            ]
        )
        result = self._bq_client().query(sql, job_config=job_config).result()
        rows = [dict(row.items()) for row in result]
        if rows:
            return rows
        # Last resort: latest manuals.
        fallback = self._bq_client().query(
            f"SELECT doc_id, category, title, content, source_uri, 1.0 AS distance "
            f"FROM `{self.config.knowledge_table}` LIMIT {int(top_k)}"
        ).result()
        return [dict(row.items()) for row in fallback]


def search_fraud_knowledge(
    project_id: str,
    query: str,
    top_k: int = VECTOR_TOP_K,
    location: str = "asia-northeast1",
    dataset: str = "dwh_prod",
) -> List[Dict[str, Any]]:
    config = AgentConfig(project_id=project_id, location=location, dataset=dataset)
    return BigQueryKnowledgeStore(config).search(query, top_k=top_k)
