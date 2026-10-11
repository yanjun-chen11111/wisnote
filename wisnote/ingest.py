"""入库流水线：上传 → 解析 → 切块 → 向量化 → 元数据落库

原文件存 data/uploads/user_{id}/，SQLite 记元数据，ChromaDB 存向量。
"""

import shutil
import uuid
from pathlib import Path

from wisnote.chunker import chunk_blocks
from wisnote.config import UPLOADS_DIR, ensure_dirs
from wisnote.db import get_conn
from wisnote.parsers import parse_file
from wisnote.vectorstore import get_store


def get_or_create_course(user_id: int, name: str) -> int:
    """获取课程分类，不存在则创建，返回 course_id"""
    name = name.strip()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM courses WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO courses (user_id, name) VALUES (?, ?)", (user_id, name)
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def ingest_document(
    user_id: int,
    file_path: Path | str,
    course_name: str | None = None,
    title: str | None = None,
) -> dict:
    """处理一个上传文件，返回入库结果信息

    流程：解析 → 切块 → 原文件归档 → SQLite 落库 → 向量入库
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")

    # 1. 解析 + 切块（先验证可解析，失败则不产生任何数据）
    blocks = parse_file(file_path)
    if not blocks:
        raise ValueError(f"未能从「{file_path.name}」中提取到文本内容")
    chunks = chunk_blocks(blocks)

    # 2. 原文件归档到用户目录
    ensure_dirs()
    user_dir = UPLOADS_DIR / f"user_{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"{uuid.uuid4().hex}{file_path.suffix.lower()}"
    shutil.copy2(file_path, dest)

    # 3. SQLite 元数据落库
    doc_title = (title or file_path.stem).strip()
    file_type = file_path.suffix.lower().lstrip(".")
    course_id = get_or_create_course(user_id, course_name) if course_name else None

    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO documents (user_id, course_id, title, filename, file_path, file_type, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'processing')",
            (user_id, course_id, doc_title, file_path.name, str(dest), file_type),
        )
        document_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO chunks (document_id, user_id, chunk_index, page, para_index, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (document_id, user_id, c.chunk_index, c.page, c.para_index, c.text)
                for c in chunks
            ],
        )
        conn.commit()
    finally:
        conn.close()

    # 4. 向量化入库（失败则回滚 SQLite 记录，避免脏数据）
    try:
        get_store().add_chunks(user_id, document_id, chunks)
    except Exception:
        _delete_document_rows(document_id, dest)
        raise

    conn = get_conn()
    try:
        conn.execute("UPDATE documents SET status = 'ready' WHERE id = ?", (document_id,))
        conn.commit()
    finally:
        conn.close()

    return {
        "document_id": document_id,
        "title": doc_title,
        "file_type": file_type,
        "blocks": len(blocks),
        "chunks": len(chunks),
    }


def _delete_document_rows(document_id: int, file_path: Path) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
    finally:
        conn.close()
    file_path.unlink(missing_ok=True)


def list_documents(user_id: int, course_name: str | None = None) -> list[dict]:
    """列出用户资料（含课程名）；传入 course_name 可按课程筛选"""
    sql = (
        "SELECT d.id, d.title, d.filename, d.file_type, d.status, d.ref_count, "
        "       d.visibility, d.created_at, c.name AS course_name "
        "FROM documents d LEFT JOIN courses c ON c.id = d.course_id "
        "WHERE d.user_id = ?"
    )
    params: list = [user_id]
    if course_name:
        sql += " AND c.name = ?"
        params.append(course_name)
    sql += " ORDER BY d.created_at DESC"
    conn = get_conn()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_courses(user_id: int) -> list[dict]:
    """列出用户的全部课程分类（含每门课的资料数）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, c.name, c.created_at, "
            "       (SELECT COUNT(*) FROM documents d WHERE d.course_id = c.id) AS doc_count "
            "FROM courses c WHERE c.user_id = ? ORDER BY c.name",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def update_document(user_id: int, document_id: int, new_file_path: Path | str) -> dict:
    """更新资料：用新文件替换原文件（保留课程分类与标题），重新解析切块与向量化"""
    new_file_path = Path(new_file_path)
    if not new_file_path.exists():
        raise FileNotFoundError(f"文件不存在：{new_file_path}")

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT title, course_id, file_path FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("资料不存在或不属于当前用户")

    # 先解析新文件，失败则原资料保持不变
    blocks = parse_file(new_file_path)
    if not blocks:
        raise ValueError(f"未能从「{new_file_path.name}」中提取到文本")
    chunks = chunk_blocks(blocks)

    old_path = Path(row["file_path"])

    # 清掉旧的切块与向量
    get_store().delete_document(user_id, document_id)
    conn = get_conn()
    try:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.commit()
    finally:
        conn.close()

    # 归档新文件
    ensure_dirs()
    user_dir = UPLOADS_DIR / f"user_{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"{uuid.uuid4().hex}{new_file_path.suffix.lower()}"
    shutil.copy2(new_file_path, dest)

    # 写回元数据与新切块
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE documents SET filename = ?, file_path = ?, file_type = ?, status = 'processing' "
            "WHERE id = ?",
            (
                new_file_path.name,
                str(dest),
                new_file_path.suffix.lower().lstrip("."),
                document_id,
            ),
        )
        conn.executemany(
            "INSERT INTO chunks (document_id, user_id, chunk_index, page, para_index, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (document_id, user_id, c.chunk_index, c.page, c.para_index, c.text)
                for c in chunks
            ],
        )
        conn.commit()
    finally:
        conn.close()

    get_store().add_chunks(user_id, document_id, chunks)

    conn = get_conn()
    try:
        conn.execute("UPDATE documents SET status = 'ready' WHERE id = ?", (document_id,))
        conn.commit()
    finally:
        conn.close()
    old_path.unlink(missing_ok=True)

    return {
        "document_id": document_id,
        "title": row["title"],
        "blocks": len(blocks),
        "chunks": len(chunks),
    }


def bump_ref_counts(document_ids: set[int] | list[int]) -> None:
    """统计资料被引用次数（回答命中一次即 +1，用于"热门资料"与元数据展示）"""
    ids = {int(i) for i in document_ids if i is not None}
    if not ids:
        return
    conn = get_conn()
    try:
        conn.executemany(
            "UPDATE documents SET ref_count = ref_count + 1 WHERE id = ?",
            [(i,) for i in ids],
        )
        conn.commit()
    finally:
        conn.close()


def delete_document(user_id: int, document_id: int) -> None:
    """删除资料：向量 + 元数据 + 原文件"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT file_path FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("资料不存在或不属于当前用户")

    get_store().delete_document(user_id, document_id)
    conn = get_conn()
    try:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
    finally:
        conn.close()
    Path(row["file_path"]).unlink(missing_ok=True)
