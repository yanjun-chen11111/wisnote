"""混合检索：语义向量 + BM25 关键词（RRF 融合），带文档溯源信息"""

from wisnote.config import HYBRID_RETRIEVAL, SEARCH_TOP_K
from wisnote.db import get_conn
from wisnote.hybrid import hybrid_search
from wisnote.vectorstore import get_store


def search_notes(
    user_id: int,
    query: str,
    top_k: int = SEARCH_TOP_K,
    hybrid: bool | None = None,
) -> list[dict]:
    """检索用户笔记，返回按相关度排序的结果，附带文档标题等溯源信息

    - hybrid=True（默认，由 config.HYBRID_RETRIEVAL 决定）：
      语义 + BM25 的 RRF 融合结果，专有名词/ID 等字面 query 不再漏检；
    - hybrid=False：纯语义检索（评测脚本用作基线）。
    """
    query = query.strip()
    if not query:
        return []
    if hybrid is None:
        hybrid = HYBRID_RETRIEVAL

    if hybrid:
        hits = hybrid_search(user_id, query, top_k)
    else:
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

    # 防御性过滤：document_id 指向已删除文档的命中是 Chroma 历史残留
    # （早期删除功能未同步清向量库，僵尸向量无法自愈），直接丢弃；
    # 否则会以"未知文档"身份混进结果，挤占 top-k 名额并污染引用展示。
    hits = [h for h in hits if h["document_id"] is None or h["document_id"] in titles]

    for h in hits:
        info = titles.get(h["document_id"], {})
        h["title"] = info.get("title", "未知文档")
        h["filename"] = info.get("filename", "")
        h["file_type"] = info.get("file_type", "")
    return hits
