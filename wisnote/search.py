"""语义检索：在用户自己的笔记中搜索与问题相关的片段"""

from wisnote.config import SEARCH_TOP_K
from wisnote.db import get_conn
from wisnote.vectorstore import get_store


def search_notes(user_id: int, query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """语义搜索，返回按相似度排序的结果，附带文档标题等溯源信息"""
    query = query.strip()
    if not query:
        return []
    hits = get_store().search(user_id, query, top_k)
    if not hits:
        return []

    # 批量取文档标题（避免逐条查库）
    doc_ids = {h["document_id"] for h in hits if h["document_id"] is not None}
    titles: dict[int, dict] = {}
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        conn = get_conn()
        try:
            rows = conn.execute(
                f"SELECT id, title, filename, file_type FROM documents WHERE id IN ({placeholders})",
                tuple(doc_ids),
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            titles[r["id"]] = dict(r)

    for h in hits:
        info = titles.get(h["document_id"], {})
        h["title"] = info.get("title", "未知文档")
        h["filename"] = info.get("filename", "")
        h["file_type"] = info.get("file_type", "")
    return hits
