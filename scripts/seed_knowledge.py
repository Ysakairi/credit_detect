"""規程 Markdown を埋め込み、``fraud_investigation_knowledge`` へ投入する。

【Agent Engine 上の位置づけ】
RAG ノードが空ヒットにならないための初期コーパス投入。グラフ実行パスは SELECT のみなので、
書き込みをこのスクリプト（と UI アップロード）に隔離する。VECTOR INDEX 作成は行数が少ない
と失敗するため必須にしない。失敗しても VECTOR_SEARCH はブルートフォースで動く。

【主な関数構成】
- parse_args: ソースディレクトリと追加ファイル
- main: チャンク → dry-run または BQ upsert → 任意で IVF インデックス
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.config import AgentConfig
from agent.tools.bq_vector_search import BigQueryKnowledgeStore, InMemoryKnowledgeStore
from agent.tools.knowledge_ingest import documents_from_directory, documents_from_file


def parse_args() -> argparse.Namespace:
    """本番投入とチャンク確認（dry-run）を同じ入口にし、ID 設計の食い違いを防ぐ。

    Returns:
        プロジェクト設定とソースパスを含む Namespace。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--location", default=os.environ.get("LOCATION", "asia-northeast1"))
    parser.add_argument("--dataset", default=os.environ.get("DATASET_ID", "dwh_prod"))
    parser.add_argument(
        "--source-dir",
        default=str(ROOT / "knowledge"),
        help="Markdown/text manuals to embed",
    )
    parser.add_argument("--file", action="append", default=[], help="Extra file path")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    """チャンクを作り、dry-run ならメモリ検索、本番なら冪等 UPSERT する。

    dry-run は GCP 無しで「0.85 / V14」がヒットするかを確認するため。

    Returns:
        None。進捗は stdout。
    """
    args = parse_args()
    docs = documents_from_directory(Path(args.source_dir))
    for extra in args.file:
        docs.extend(documents_from_file(Path(extra)))
    print(f"Prepared {len(docs)} chunks from {args.source_dir}")
    if args.dry_run:
        store = InMemoryKnowledgeStore()
        store.upsert(docs)
        hits = store.search("fraud_probability 0.85 V14 PCI-DSS")
        for hit in hits:
            print(f"- {hit.get('title')} ({hit.get('category')})")
        return
    if not args.project_id:
        raise SystemExit("PROJECT_ID is required unless --dry-run")
    config = AgentConfig(
        project_id=args.project_id,
        location=args.location,
        dataset=args.dataset,
        backend="local",
    )
    store = BigQueryKnowledgeStore(config)
    n = store.upsert(docs)
    print(f"Upserted {n} rows into {config.knowledge_table}")
    try:
        sql = f"""
        CREATE VECTOR INDEX IF NOT EXISTS knowledge_vector_index
        ON `{config.knowledge_table}`(embedding)
        OPTIONS(distance_type='COSINE', index_type='IVF')
        """
        store._bq_client().query(sql).result()
        print("Vector index ensured (IVF / COSINE)")
    except Exception as exc:
        print(f"Vector index skipped ({exc}). VECTOR_SEARCH still works on small tables.")


if __name__ == "__main__":
    main()
