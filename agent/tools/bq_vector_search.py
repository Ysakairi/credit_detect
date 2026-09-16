"""調査マニュアルの Hybrid RAG（BigQuery VECTOR_SEARCH）。

【Agent Engine 上の位置づけ】
RAG ノードの実体。Vertex AI Vector Search（Index Endpoint）は常時課金のため使わず、
埋め込みを BQ テーブルに持ち VECTOR_SEARCH する。PoC の文書数ではインデックス無しの
ブルートフォースで足りる。クエリ時も同じ text-embedding-004 を Vertex SDK で呼び、
BQ リモートモデル / Cloud Resource Connection を必須にしない。

Agent Engine 上で埋め込み API が落ちても調査を止めないよう、キーワード検索と
「最新 N 件」へフォールバックする。規程ゼロは Reflection が空論になるため。

【主な関数構成】
- KnowledgeStore: search / upsert の Protocol
- chunk_text: 見出し優先のチャンク（埋め込み長と境界情報の両立）
- InMemoryKnowledgeStore: mock / テスト。CJK n-gram で日本語クエリを拾う
- BigQueryKnowledgeStore: 本番テーブルの upsert と VECTOR_SEARCH
- search_fraud_knowledge: スクリプト用ワンショット検索
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
    """グラフと投入スクリプトが共有するナレッジ口。"""

    def search(self, query: str, top_k: int = VECTOR_TOP_K) -> List[Dict[str, Any]]:
        ...

    def upsert(self, documents: Sequence[Dict[str, Any]]) -> int:
        ...


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> List[str]:
    """規程を埋め込みサイズへ切る。見出し境界を優先し、条項の途中切断を減らす。

    overlap は隣接チャンクで VECTOR_SEARCH が条番号と本文を同時に拾えるようにするため。
    900 文字は text-embedding-004 の実用長と、プロンプトへ載せる抜粋量の妥協点。

    Args:
        text: マニュアル全文。
        chunk_size: 1 チャンクの最大文字数。
        overlap: 長大パートを切るときの重複文字数。

    Returns:
        空でないチャンク文字列のリスト。
    """
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
    """モック試験で埋め込みを入れたときの類似度。ゼロベクトル除算を避け 0 に倒す。

    Args:
        a: クエリベクトル。
        b: 文書ベクトル。

    Returns:
        0〜1 近傍のコサイン類似度。空なら 0.0。
    """
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(a[i]) ** 2 for i in range(n))) or 1e-12
    nb = math.sqrt(sum(float(b[i]) ** 2 for i in range(n))) or 1e-12
    return dot / (na * nb)


def _tokens(text: str) -> set[str]:
    """日本語クエリでも mock RAG がヒットするよう、ASCII 語と CJK n-gram を混ぜる。

    形態素解析器を Agent Engine / CI の依存に足さないための簡易トークナイザ。

    Args:
        text: クエリまたは文書。

    Returns:
        照合用トークン集合。
    """
    blob = (text or "").lower()
    ascii_toks = re.findall(r"[a-z0-9_]{2,}", blob)
    cjk_runs = re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]+", blob)
    grams: List[str] = []
    for run in cjk_runs:
        grams.append(run)
        if len(run) >= 2:
            grams.extend(run[i : i + 2] for i in range(len(run) - 1))
        if len(run) >= 3:
            grams.extend(run[i : i + 3] for i in range(len(run) - 2))
    return set(ascii_toks + grams)


class InMemoryKnowledgeStore:
    """埋め込み無しでもタイトル一致を強くし、アップロード資料が mock UI で検索できるようにする。"""

    def __init__(self, documents: Optional[List[Dict[str, Any]]] = None):
        """初期コーパスを受け取る。テストが最小文書だけを載せるため。

        Args:
            documents: 省略時は空。
        """
        self.documents: List[Dict[str, Any]] = list(documents or [])

    def upsert(self, documents: Sequence[Dict[str, Any]]) -> int:
        """同一 doc_id は置換し、再アップロードを冪等にする（BQ 側 DELETE+INSERT と同じ契約）。

        Args:
            documents: doc_id 必須の文書 dict。

        Returns:
            追加（置換含む）件数。
        """
        added = 0
        existing = {d.get("doc_id") for d in self.documents}
        for doc in documents:
            if doc.get("doc_id") in existing:
                self.documents = [d for d in self.documents if d.get("doc_id") != doc.get("doc_id")]
            self.documents.append(dict(doc))
            added += 1
        return added

    def search(self, query: str, top_k: int = VECTOR_TOP_K) -> List[Dict[str, Any]]:
        """distance を「小さいほど近い」に揃え、BQ VECTOR_SEARCH の COSINE 距離と UI 表示を一致させる。

        Args:
            query: 自然言語の調査指示。
            top_k: 返す件数。プロンプト長を抑えるため既定 3。

        Returns:
            distance 昇順のヒット。
        """
        query_tokens = _tokens(query)
        scored: List[Dict[str, Any]] = []
        for doc in self.documents:
            item = dict(doc)
            emb = doc.get("embedding")
            if emb and any(emb):
                # Unused unless embeddings are populated in mock tests.
                item["distance"] = 1.0
            doc_tokens = _tokens(f"{doc.get('title', '')} {doc.get('content', '')}")
            overlap = len(query_tokens & doc_tokens)
            title_hit = 1 if any(tok in (doc.get("title") or "").lower() for tok in query_tokens) else 0
            item["distance"] = 1.0 / (1.0 + overlap + 2 * title_hit)
            scored.append(item)
        scored.sort(key=lambda d: d.get("distance", 1.0))
        return scored[:top_k]


class BigQueryKnowledgeStore:
    """本番ナレッジテーブル。Agent Engine の RAG ノードと UI アップロードが同じ UPSERT 契約を使う。"""

    def __init__(self, config: AgentConfig):
        """テーブル ID と埋め込みモデル名だけ保持する。Client は遅延。

        Args:
            config: knowledge_table と Vertex ロケーションを含む設定。
        """
        self.config = config
        self._bq = None
        self._embedding_model = None

    def _bq_client(self):
        """set_up / pickle 後まで Client を開かない。

        Returns:
            bigquery.Client。
        """
        if self._bq is None:
            from google.cloud import bigquery

            self._bq = bigquery.Client(
                project=self.config.project_id, location=self.config.location
            )
        return self._bq

    def ensure_table(self) -> None:
        """初回検索や投入でテーブルが無くても RAG を落とさない（exists_ok で作る）。

        Returns:
            None。副作用でテーブルを保証する。
        """
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
        """クエリと文書を同じ 768 次元に揃え、VECTOR_SEARCH の次元不一致を防ぐ。

        失敗時はゼロベクトルを返し、呼び出し側がキーワード検索へ倒せるようにする。
        バッチ 16 は埋め込み API のペイロード制限対策。

        Args:
            texts: 埋め込む文字列。

        Returns:
            各テキストの 768-d ベクトル。失敗時はゼロ埋め。
        """
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
        """同一 doc_id を DELETE してから INSERT し、再シードと UI 再アップロードを冪等にする。

        埋め込みが無い行だけ API を呼び、既にベクトルを持つ再投入の課金を避ける。

        Args:
            documents: doc_id / content を含む文書。category 欠落は UPLOADED。

        Returns:
            INSERT した行数。

        Raises:
            RuntimeError: insert_rows_json がエラーを返したとき。
        """
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
        """埋め込みが得られたときだけベクトル検索し、ゼロベクトルならキーワードへ倒す。

        Args:
            query: 調査指示。
            top_k: ヒット件数。

        Returns:
            doc_id / category / title / content / distance を含む dict リスト。
        """
        self.ensure_table()
        vectors = self.embed_texts([query])
        query_embedding = vectors[0] if vectors else []
        if query_embedding and any(query_embedding):
            return self._vector_search(query_embedding, top_k)
        return self._keyword_search(query, top_k)

    def _vector_search(self, embedding: List[float], top_k: int) -> List[Dict[str, Any]]:
        """BQ VECTOR_SEARCH（COSINE）。インデックス未作成でも小規模ならブルートフォースで動く。

        失敗時はキーワードへフォールバックする。埋め込み配列を LIKE に渡しても無意味なため、
        呼び出し側 search() がクエリ文字列を持つ経路と、本メソッド内の簡易フォールバックを分ける。

        Args:
            embedding: 768-d クエリベクトル。
            top_k: 上位件数。

        Returns:
            距離付きヒット。失敗時はキーワード検索結果。
        """
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
        """埋め込み失敗時でも規程を 1 件以上返し、Reflection が「ヒットなし」だけにならないようにする。

        LIKE 0 件なら最新行へ倒す。空テーブル以外で RAG ノードが空リストを返すのを最後の手段で防ぐ。

        Args:
            query: LIKE 用文字列。ベクトル失敗経路では意味の薄い断片になり得る。
            top_k: 上限件数。

        Returns:
            distance=1.0 のヒット（順位情報は無い）。
        """
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
    """グラフ外から同じストア実装で検索するショートカット。

    Args:
        project_id: GCP プロジェクト。
        query: 検索文。
        top_k: 件数。
        location: リージョン。
        dataset: ナレッジテーブルのデータセット。

    Returns:
        ヒット dict のリスト。
    """
    config = AgentConfig(project_id=project_id, location=location, dataset=dataset)
    return BigQueryKnowledgeStore(config).search(query, top_k=top_k)
