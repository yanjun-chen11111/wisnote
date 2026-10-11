"""学习小组（计划书模块4）：组内共享资料库 + 组内 RAG 检索 + 讨论区

按课程/考试/兴趣创建小组，成员把资料加入组内库，
组内资料统一写入 group_{id} 向量集合，实现"小组范围内的 RAG 检索"。
"""

from wisnote.config import SEARCH_TOP_K
from wisnote.db import get_conn
from wisnote.llm import LLMError, chat
from wisnote.vectorstore import get_store


def _group_collection(group_id: int):
    return get_store().client.get_or_create_collection(
        f"group_{group_id}", metadata={"hnsw:space": "cosine"}
    )


# ---------------- 小组管理 ----------------
def create_group(user_id: int, name: str, description: str = "", course: str = "") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("小组名称不能为空")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO groups (name, description, course, owner_id) VALUES (?, ?, ?, ?)",
            (name, description.strip(), course.strip(), user_id),
        )
        conn.commit()
        group_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO group_members (group_id, user_id, role) VALUES (?, ?, 'owner')",
            (group_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return group_id


def join_group(user_id: int, group_id: int) -> None:
    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        if exists:
            return
        conn.execute(
            "INSERT INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def leave_group(user_id: int, group_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND user_id = ?", (group_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def is_member(user_id: int, group_id: int) -> bool:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone() is not None
    finally:
        conn.close()


def list_groups(user_id: int | None = None) -> list[dict]:
    conn = get_conn()
    try:
        if user_id is None:
            rows = conn.execute(
                "SELECT g.*, (SELECT COUNT(*) FROM group_members m WHERE m.group_id = g.id) AS member_count "
                "FROM groups g ORDER BY g.created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT g.*, (SELECT COUNT(*) FROM group_members m WHERE m.group_id = g.id) AS member_count "
                "FROM groups g JOIN group_members m2 ON m2.group_id = g.id "
                "WHERE m2.user_id = ? ORDER BY g.created_at DESC",
                (user_id,),
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_members(group_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT u.id, u.username, m.role, m.joined_at "
            "FROM group_members m JOIN users u ON u.id = m.user_id "
            "WHERE m.group_id = ? ORDER BY m.joined_at",
            (group_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------- 组内资料库 ----------------
def add_group_document(user_id: int, group_id: int, document_id: int) -> None:
    """把资料加入小组共享库（同时写入小组向量集合）"""
    if not is_member(user_id, group_id):
        raise ValueError("只有小组成员可以添加资料")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, title, user_id FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None:
            raise ValueError("资料不存在")
        chunks = conn.execute(
            "SELECT chunk_index, page, para_index, text FROM chunks "
            "WHERE document_id = ? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()
        conn.execute(
            "INSERT OR IGNORE INTO group_documents (group_id, document_id, added_by) VALUES (?, ?, ?)",
            (group_id, document_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()

    if chunks:
        store = get_store()
        col = _group_collection(group_id)
        col.delete(where={"document_id": document_id})
        texts = [c["text"] for c in chunks]
        metas = []
        for c in chunks:
            m = {"document_id": document_id, "title": row["title"], "owner_id": row["user_id"]}
            if c["page"] is not None:
                m["page"] = c["page"]
            if c["para_index"] is not None:
                m["para_index"] = c["para_index"]
            metas.append(m)
        col.add(
            ids=[f"{document_id}_{c['chunk_index']}" for c in chunks],
            documents=texts,
            metadatas=metas,
            embeddings=store.embed(texts),
        )


def remove_group_document(user_id: int, group_id: int, document_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM group_documents WHERE group_id = ? AND document_id = ?",
            (group_id, document_id),
        )
        conn.commit()
    finally:
        conn.close()
    _group_collection(group_id).delete(where={"document_id": document_id})


def list_group_documents(group_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT d.id, d.title, d.file_type, u.username AS owner, g.added_at "
            "FROM group_documents g JOIN documents d ON d.id = g.document_id "
            "JOIN users u ON u.id = d.user_id "
            "WHERE g.group_id = ? ORDER BY g.added_at DESC",
            (group_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------- 组内检索与问答 ----------------
def search_group(user_id: int, group_id: int, query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """组内 RAG 检索：在小组共享资料中搜索"""
    if not is_member(user_id, group_id):
        raise ValueError("只有小组成员可以检索组内资料")
    query = (query or "").strip()
    if not query:
        return []
    col = _group_collection(group_id)
    if col.count() == 0:
        return []
    store = get_store()
    res = col.query(
        query_embeddings=[store.embed([query])[0]],
        n_results=min(top_k, col.count()),
    )
    return [
        {
            "title": (res["metadatas"][0][i] or {}).get("title", ""),
            "document_id": (res["metadatas"][0][i] or {}).get("document_id"),
            "page": (res["metadatas"][0][i] or {}).get("page"),
            "para_index": (res["metadatas"][0][i] or {}).get("para_index"),
            "text": res["documents"][0][i],
            "similarity": 1.0 - res["distances"][0][i],
        }
        for i in range(len(res["ids"][0]))
    ]


def ask_group(user_id: int, group_id: int, question: str, top_k: int = SEARCH_TOP_K) -> dict:
    """组内问答：基于小组共享资料生成回答（含溯源）"""
    hits = search_group(user_id, group_id, question, top_k)
    if not hits:
        return {"question": question, "answer": "小组资料库中还没有相关资料，先添加资料吧。", "sources": []}
    context = "\n\n".join(
        f"[{i}] 来源：{h['title']}"
        + (f" 第{h['page']}页" if h["page"] else "")
        + f"\n{h['text']}"
        for i, h in enumerate(hits, start=1)
    )
    try:
        answer = chat(
            [
                {
                    "role": "system",
                    "content": "你是学习小组助手，只能依据【小组资料片段】回答，不编造，"
                               "用到片段处标注来源编号如 [1]，资料不足时明确说明。",
                },
                {"role": "user", "content": f"【小组资料片段】\n{context}\n\n【问题】\n{question}"},
            ]
        )
    except LLMError as e:
        return {"question": question, "answer": f"模型调用失败：{e}", "sources": []}
    sources = [
        {
            "index": i,
            "title": h["title"],
            "document_id": h["document_id"],
            "page": h["page"],
            "para_index": h["para_index"],
            "similarity": round(h["similarity"], 4),
            "snippet": h["text"][:200],
        }
        for i, h in enumerate(hits, start=1)
    ]
    return {"question": question, "answer": answer, "sources": sources}


# ---------------- 讨论区 ----------------
def post_message(user_id: int, group_id: int, content: str) -> int:
    content = (content or "").strip()
    if not content:
        raise ValueError("消息不能为空")
    if not is_member(user_id, group_id):
        raise ValueError("只有小组成员可以发言")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO group_messages (group_id, user_id, content) VALUES (?, ?, ?)",
            (group_id, user_id, content),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_messages(group_id: int, limit: int = 100) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT m.id, m.content, m.created_at, u.username "
            "FROM group_messages m JOIN users u ON u.id = m.user_id "
            "WHERE m.group_id = ? ORDER BY m.id DESC LIMIT ?",
            (group_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in reversed(rows)]
