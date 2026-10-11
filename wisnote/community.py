"""社区功能（计划书模块4）：笔记分享、导入、互动、热门排行

重要说明（论文需如实描述）：
本项目为本地单机部署，"社区"采用**本地多用户模拟**实现——
多个账号共享同一台机器上的本地知识库，公开笔记统一写入社区向量集合，
从而实现跨账号的社区检索。不涉及网络与服务器。

可见性：private 私有 / course 课程可见 / public 公开（可被他人导入）
"""

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from wisnote.config import SEARCH_TOP_K, UPLOADS_DIR
from wisnote.db import get_conn
from wisnote.ingest import ingest_document
from wisnote.vectorstore import get_store

VISIBILITIES = ("private", "course", "public")
COMMUNITY_COLLECTION = "community"


def _community_collection():
    return get_store().client.get_or_create_collection(
        COMMUNITY_COLLECTION, metadata={"hnsw:space": "cosine"}
    )


# ---------------- 分享 ----------------
def share_document(user_id: int, document_id: int, visibility: str = "public") -> None:
    """分享笔记到社区（公开笔记的切块会写入社区向量集合，供所有人检索）"""
    if visibility not in VISIBILITIES:
        raise ValueError(f"可见性必须是 {VISIBILITIES}")

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, title, user_id FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("资料不存在或不属于当前用户")
        chunks = conn.execute(
            "SELECT chunk_index, page, para_index, text FROM chunks "
            "WHERE document_id = ? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()
    finally:
        conn.close()

    if visibility == "public":
        store = get_store()
        col = _community_collection()
        # 先清掉旧版本，避免重复分享产生冗余向量
        col.delete(where={"document_id": document_id})
        if chunks:
            texts = [c["text"] for c in chunks]
            metas = []
            for c in chunks:
                m = {"document_id": document_id, "owner_id": user_id, "title": row["title"]}
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
    else:
        # 改为非公开：从社区集合移除
        _community_collection().delete(where={"document_id": document_id})

    conn = get_conn()
    try:
        conn.execute(
            "UPDATE documents SET visibility = ?, shared_at = ? WHERE id = ?",
            (visibility, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), document_id),
        )
        conn.commit()
    finally:
        conn.close()


def unshare_document(user_id: int, document_id: int) -> None:
    """取消分享"""
    _community_collection().delete(where={"document_id": document_id})
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE documents SET visibility = 'private', shared_at = NULL "
            "WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------- 社区检索与浏览 ----------------
def search_community(query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """在所有公开笔记中做语义检索（跨账号）"""
    query = (query or "").strip()
    if not query:
        return []
    col = _community_collection()
    if col.count() == 0:
        return []
    store = get_store()
    res = col.query(
        query_embeddings=[store.embed([query])[0]],
        n_results=min(top_k, col.count()),
    )
    hits = []
    for i in range(len(res["ids"][0])):
        meta = res["metadatas"][0][i] or {}
        hits.append(
            {
                "document_id": meta.get("document_id"),
                "title": meta.get("title", ""),
                "owner_id": meta.get("owner_id"),
                "text": res["documents"][0][i],
                "page": meta.get("page"),
                "para_index": meta.get("para_index"),
                "similarity": 1.0 - res["distances"][0][i],
            }
        )
    return hits


def list_public_documents(limit: int = 50) -> list[dict]:
    """浏览社区公开笔记（含作者名、点赞数）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT d.id, d.title, d.file_type, d.visibility, d.shared_at, "
            "       d.summary, d.tags, u.username AS owner, "
            "       (SELECT COUNT(*) FROM likes l WHERE l.document_id = d.id) AS like_count, "
            "       (SELECT COUNT(*) FROM comments c WHERE c.document_id = d.id) AS comment_count "
            "FROM documents d JOIN users u ON u.id = d.user_id "
            "WHERE d.visibility = 'public' "
            "ORDER BY like_count DESC, d.shared_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def hot_documents(limit: int = 10) -> list[dict]:
    """热门排行：按点赞数排序"""
    return list_public_documents(limit)


# ---------------- 导入他人笔记 ----------------
def import_document(user_id: int, document_id: int) -> dict:
    """一键导入他人的公开笔记：复制到自己库里，保留原始来源信息"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT d.title, d.file_path, d.filename, d.visibility, u.username AS owner "
            "FROM documents d JOIN users u ON u.id = d.user_id WHERE d.id = ?",
            (document_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("资料不存在")
    if row["visibility"] != "public":
        raise ValueError("该笔记未公开，无法导入")
    if not Path(row["file_path"]).exists():
        raise ValueError("原始文件已丢失，无法导入")

    # 复制原文件到临时位置再入库（避免与原文件相互干扰）
    src = Path(row["file_path"])
    tmp_dir = UPLOADS_DIR / "_import_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"{uuid.uuid4().hex}{src.suffix.lower()}"
    shutil.copy2(src, tmp_file)

    try:
        r = ingest_document(user_id, tmp_file, course_name=f"来自 @{row['owner']}", title=row["title"])
    finally:
        tmp_file.unlink(missing_ok=True)

    conn = get_conn()
    try:
        conn.execute(
            "UPDATE documents SET original_author = ?, origin_doc_id = ? WHERE id = ?",
            (row["owner"], document_id, r["document_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    r["original_author"] = row["owner"]
    r["origin_doc_id"] = document_id
    return r


# ---------------- 互动：点赞 / 收藏 / 评论 / 关注 ----------------
def toggle_like(user_id: int, document_id: int) -> bool:
    """点赞/取消点赞，返回操作后是否为已点赞"""
    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM likes WHERE document_id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM likes WHERE document_id = ? AND user_id = ?",
                (document_id, user_id),
            )
            liked = False
        else:
            conn.execute(
                "INSERT INTO likes (document_id, user_id) VALUES (?, ?)",
                (document_id, user_id),
            )
            liked = True
        conn.commit()
    finally:
        conn.close()
    return liked


def like_count(document_id: int) -> int:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM likes WHERE document_id = ?", (document_id,)
        ).fetchone()["n"]
    finally:
        conn.close()


def has_liked(user_id: int, document_id: int) -> bool:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM likes WHERE document_id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone() is not None
    finally:
        conn.close()


def toggle_favorite(user_id: int, document_id: int) -> bool:
    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM favorites WHERE document_id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM favorites WHERE document_id = ? AND user_id = ?",
                (document_id, user_id),
            )
            fav = False
        else:
            conn.execute(
                "INSERT INTO favorites (document_id, user_id) VALUES (?, ?)",
                (document_id, user_id),
            )
            fav = True
        conn.commit()
    finally:
        conn.close()
    return fav


def list_favorites(user_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT d.id, d.title, d.file_type, d.summary, u.username AS owner, f.created_at "
            "FROM favorites f JOIN documents d ON d.id = f.document_id "
            "JOIN users u ON u.id = d.user_id "
            "WHERE f.user_id = ? ORDER BY f.created_at DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def add_comment(user_id: int, document_id: int, content: str) -> int:
    content = (content or "").strip()
    if not content:
        raise ValueError("评论内容不能为空")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO comments (document_id, user_id, content) VALUES (?, ?, ?)",
            (document_id, user_id, content),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_comments(document_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, c.content, c.created_at, u.username "
            "FROM comments c JOIN users u ON u.id = c.user_id "
            "WHERE c.document_id = ? ORDER BY c.id",
            (document_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_comment(user_id: int, comment_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM comments WHERE id = ? AND user_id = ?", (comment_id, user_id))
        conn.commit()
    finally:
        conn.close()


def toggle_follow(follower_id: int, followee_id: int) -> bool:
    if follower_id == followee_id:
        raise ValueError("不能关注自己")
    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?",
            (follower_id, followee_id),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM follows WHERE follower_id = ? AND followee_id = ?",
                (follower_id, followee_id),
            )
            following = False
        else:
            conn.execute(
                "INSERT INTO follows (follower_id, followee_id) VALUES (?, ?)",
                (follower_id, followee_id),
            )
            following = True
        conn.commit()
    finally:
        conn.close()
    return following


def list_users(exclude_user_id: int | None = None) -> list[dict]:
    """列出平台用户（本地多账号模拟社区用）"""
    conn = get_conn()
    try:
        if exclude_user_id is None:
            rows = conn.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, username, created_at FROM users WHERE id != ? ORDER BY id",
                (exclude_user_id,),
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_following(user_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT u.id, u.username FROM follows f JOIN users u ON u.id = f.followee_id "
            "WHERE f.follower_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
