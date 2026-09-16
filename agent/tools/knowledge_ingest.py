"""アップロード資料をナレッジテーブル行へ変換する。

【Agent Engine 上の位置づけ】
調査グラフ本体は読取専用。書き込みは UI と seed スクリプトだけが行う。
doc_id を title+uri のハッシュとチャンク番号で安定させ、再投入が DELETE+INSERT で
重複しないようにする。カテゴリ推定は RAG のフィルタとレポートの「どの種の根拠か」表示用。

【主な関数構成】
- slugify: doc_id の安全な接頭辞
- infer_category: POLICY / PCI_DSS / INCIDENT_CASE のヒューリスティック
- documents_from_text / documents_from_file / documents_from_directory: チャンク行の生成
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.tools.bq_vector_search import chunk_text


def slugify(value: str) -> str:
    """BQ の doc_id に使えない空白・記号を落とし、パスやタイトルの差で ID が壊れないようにする。

    Args:
        value: タイトルやファイル名。

    Returns:
        80 文字以内のスラッグ。空なら ``doc``。
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "doc"


def infer_category(title: str, text: str) -> str:
    """アナリストがカテゴリを選ばなくても、規程とインシデント事例を混在検索しすぎないようにする。

    判定できないものは POLICY に倒す。誤って INCIDENT にすると閾値条項が事例扱いになり、
    Reflection の根拠が弱くなるため。

    Args:
        title: 文書タイトル。
        text: 本文。

    Returns:
        POLICY / PCI_DSS / INCIDENT_CASE のいずれか。
    """
    blob = f"{title}\n{text}".upper()
    if "POL-SEC" in blob or "POL_SEC" in blob or "SOP" in blob or "EVALUAT" in blob or "PR-AUC" in blob:
        return "POLICY"
    if "PCI" in blob or "DSS" in blob:
        return "PCI_DSS"
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
    """本文をチャンクし、再アップロードで同じ doc_id になる行を作る。

    ハッシュに content を入れないのは、誤字修正のたびに ID が変わって古いチャンクが
    孤児として残るのを防ぐため。チャンク番号は見出し分割結果に依存する。

    Args:
        text: マニュアル本文。
        title: 表示名と ID 接頭辞。
        source_uri: ファイルパスやアップロード名。ID の安定化に使う。
        category: 省略時は infer_category。

    Returns:
        embedding 無しの知識行。ストア側で埋め込む。
    """
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
    """ファイル名を title にし、パスを source_uri にして ID を安定させる。

    Args:
        path: Markdown / テキストファイル。

    Returns:
        チャンク済み知識行。
    """
    text = path.read_text(encoding="utf-8")
    return documents_from_text(
        text,
        title=path.stem,
        source_uri=str(path),
    )


def documents_from_directory(directory: Path) -> List[Dict[str, Any]]:
    """初期コーパス投入用。md/txt 以外を無視し、バイナリを埋め込み API に送らない。

    Args:
        directory: knowledge/ など。

    Returns:
        配下ファイルを結合した知識行。
    """
    docs: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        docs.extend(documents_from_file(path))
    return docs
