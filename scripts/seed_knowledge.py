#!/usr/bin/env python3
"""Create the knowledge table and insert manuals as text-embedding-004 vectors."""

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
