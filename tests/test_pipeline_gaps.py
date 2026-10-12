"""补测：范围内错误路径与存档分支（覆盖缺口收口）"""

from pathlib import Path

import pytest

import wisnote.ingest as ing
from wisnote.db import get_conn
from wisnote.ingest import delete_document, ingest_document, list_documents


def test_vectorization_failure_rolls_back(user_id, tmp_path, monkeypatch):
    """G1 错误路径：向量化失败 -> 回滚 SQLite 记录并删除已归档文件"""
    p = tmp_path / "g1.txt"
    p.write_text("向量化失败回滚测试内容。" * 5, encoding="utf-8")

    class _BoomStore:
        def add_chunks(self, *a, **k):
            raise RuntimeError("vector store down")

    monkeypatch.setattr(ing, "get_store", lambda: _BoomStore())

    with pytest.raises(RuntimeError, match="vector store down"):
        ingest_document(user_id, str(p), title="G1")

    conn = get_conn()
    try:
        n_docs = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        n_chunks = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n_docs == 0, "失败后不得残留 documents 行"
    assert n_chunks == 0, "失败后不得残留 chunks 行"
    # 已归档文件也应被清理：检查临时 uploads 目录中没有残留
    uploads = Path(ing.UPLOADS_DIR) / f"user_{user_id}"
    leftovers = list(uploads.glob("*.txt")) if uploads.exists() else []
    assert leftovers == [], f"回滚应删除归档文件: {leftovers}"


def test_delete_document_removes_everything(user_id, tmp_path):
    """G2 状态迁移：删除资料 -> 向量/元数据/原文件三清"""
    p = tmp_path / "g2.txt"
    p.write_text("删除测试内容。" * 5, encoding="utf-8")
    r = ingest_document(user_id, str(p), title="G2")
    doc = next(d for d in list_documents(user_id) if d["title"] == "G2")
    file_path = doc["file_path"]
    assert Path(file_path).exists()

    delete_document(user_id, r["document_id"])

    conn = get_conn()
    try:
        n_docs = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE id = ?", (r["document_id"],)
        ).fetchone()["n"]
        n_chunks = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE document_id = ?", (r["document_id"],)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n_docs == 0 and n_chunks == 0
    assert not Path(file_path).exists(), "原文件应被删除"


def test_delete_nonexistent_document(user_id):
    """G3 非法迁移：删除不存在的资料 -> ValueError"""
    with pytest.raises(ValueError, match="不属于当前用户"):
        delete_document(user_id, 999999)


def test_stream_explain_saves_to_session(user_id, tmp_path, monkeypatch):
    """G4 存档分支：save=True -> 讲解与拒答都写入聊天会话（含来源）"""
    import wisnote.explain as ex
    from wisnote.chat import create_session, get_messages

    calls = {"llm": []}

    def fake_stream(messages, options=None):
        calls["llm"].append(messages)
        yield "存档测试讲解。"

    monkeypatch.setattr(ex, "search_notes", lambda *a, **k: [])
    monkeypatch.setattr(ex, "chat_stream", fake_stream)

    sid = create_session(user_id, "存档会话")
    "".join(ex.stream_explain(user_id, "量子纠缠", mode="plain",
                              session_id=sid, save=True, source="auto"))
    msgs = get_messages(sid)
    assert len(msgs) == 2, f"应存 2 条（用户提问+AI回答）: {len(msgs)}"
    assert msgs[0]["role"] == "user" and "通俗讲解" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"].startswith("ℹ️"), "auto 降级前缀应一并存档"
