"""Turn uploaded manuals into knowledge-table rows."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.tools.bq_vector_search import chunk_text


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "doc"


def infer_category(title: str, text: str) -> str:
    blob = f"{title}\n{text}".upper()
    if "PCI" in blob or "DSS" in blob:
        return "PCI_DSS"
    if "SOP" in blob or "EVALUAT" in blob or "PR-AUC" in blob:
        return "POLICY"
    if "INCIDENT" in blob or "CARD TEST" in blob or "BIN" in blob:
        return "INCIDENT_CASE"
    return "POLICY"


def documents_from_text(
    text: str,
    *,
    title: str,
    source_uri: str = "",
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    chunks = chunk_text(text)
    category = category or infer_category(title, text)
    docs: List[Dict[str, Any]] = []
    digest = hashlib.sha1(f"{title}:{source_uri}".encode("utf-8")).hexdigest()[:8]
    base = slugify(title)
    for i, chunk in enumerate(chunks):
        docs.append(
            {
                "doc_id": f"{base}-{digest}-{i:03d}",
                "category": category,
                "title": f"{title} #{i + 1}" if len(chunks) > 1 else title,
                "content": chunk,
                "source_uri": source_uri,
            }
        )
    return docs


def documents_from_file(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return documents_from_text(
        text,
        title=path.stem,
        source_uri=str(path),
    )


def documents_from_directory(directory: Path) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        docs.extend(documents_from_file(path))
    return docs
