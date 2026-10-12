"""C. 入库流水线测试：决策表（文件类型 × 有无文字）+ 边界值"""

import pytest

from wisnote.db import get_conn
from wisnote.ingest import ingest_document, list_documents


def _write(tmp_path, name: str, content: bytes | str) -> object:
    p = tmp_path / name
    if isinstance(content, str):
        p.write_text(content, encoding="utf-8")
    else:
        p.write_bytes(content)
    return p


def _doc(docs: list[dict], title: str) -> dict:
    return next(d for d in docs if d["title"] == title)


# ---------- 决策表：{图片,文本} × {有字,无字} ----------

def test_image_with_text_full_pipeline(user_id, make_text_image):
    """C1 图片×有字 -> 完整流水线：切块>0、chunk_count>0、status=ready、文件归档"""
    from pathlib import Path

    img = make_text_image("c1.png", [(40, "操作系统课程笔记"), (200, "页面置换算法LRU")])
    r = ingest_document(user_id, str(img), course_name="操作系统", title="C1图")
    assert r["chunks"] > 0, f"应产生切块: {r}"
    doc = _doc(list_documents(user_id), "C1图")
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == r["chunks"] > 0
    assert Path(doc["file_path"]).exists(), "原文件应已归档"


def test_image_without_text_degrades(user_id, tmp_path):
    """C2 图片×无字 -> 降级：chunks=0、chunk_count=0、status=ready、不报错"""
    from PIL import Image

    p = tmp_path / "blank.png"
    Image.new("RGB", (320, 160), "white").save(p)
    r = ingest_document(user_id, str(p), title="C2空白图")
    assert r["chunks"] == 0
    doc = _doc(list_documents(user_id), "C2空白图")
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 0


def test_text_document_normal(user_id, tmp_path):
    """C3 文本×有字 -> 正常入库"""
    p = _write(tmp_path, "c3.txt", "这是一段足够长的中文文本，用于验证文本入库流水线的正常行为。" * 3)
    r = ingest_document(user_id, str(p), title="C3文本")
    assert r["chunks"] > 0


def test_text_empty_rolls_back(user_id, tmp_path):
    """C4 文本×无字 -> ValueError 且零残留（无 documents 行）"""
    p = _write(tmp_path, "c4.txt", "")
    with pytest.raises(ValueError, match="提取到文本"):
        ingest_document(user_id, str(p), title="C4空文本")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE user_id = ?", (user_id,)
        ).fetchone()
        assert row["n"] == 0, "失败入库不应残留 documents 行"
    finally:
        conn.close()


def test_unsupported_extension(user_id, tmp_path):
    """C5 无效类：不支持的扩展名 -> ValueError"""
    p = _write(tmp_path, "c5.xyz", "whatever")
    with pytest.raises(ValueError, match="暂不支持"):
        ingest_document(user_id, str(p), title="C5")


def test_uppercase_image_ext(user_id, tmp_path):
    """C6 边界：大写扩展名 .PNG -> 按图片处理（无字降级而非解析报错）"""
    from PIL import Image

    p = tmp_path / "c6.PNG"
    Image.new("RGB", (320, 160), "white").save(p)
    r = ingest_document(user_id, str(p), title="C6大写")
    assert r["file_type"] == "png"
    assert r["chunks"] == 0


# ---------- 边界值 ----------

def test_empty_course_name_means_none(user_id, tmp_path):
    """C7 边界：course_name 为空串 -> 不建课程（course_id 为 NULL）"""
    p = _write(tmp_path, "c7.txt", "一些内容用于入库测试。" * 5)
    ingest_document(user_id, str(p), course_name="", title="C7")
    doc = _doc(list_documents(user_id), "C7")
    assert doc["course_name"] is None


def test_title_defaults_to_stem(user_id, tmp_path):
    """C8 默认值：未传 title -> 用文件名（去扩展名）"""
    p = _write(tmp_path, "默认标题.txt", "默认标题内容。" * 5)
    ingest_document(user_id, str(p))
    assert _doc(list_documents(user_id), "默认标题")["title"] == "默认标题"


def test_list_documents_returns_file_path_and_chunk_count(user_id, tmp_path):
    """C9 回归：list_documents 必须返回 file_path 与 chunk_count（曾因缺失导致 UI KeyError）"""
    p = _write(tmp_path, "c9.txt", "回归测试内容。" * 5)
    ingest_document(user_id, str(p), title="C9")
    doc = _doc(list_documents(user_id), "C9")
    assert "file_path" in doc and doc["file_path"]
    assert doc["chunk_count"] > 0


def test_filter_by_course(user_id, tmp_path):
    """C10 筛选：按课程名过滤列表"""
    p = _write(tmp_path, "c10.txt", "过滤测试内容。" * 5)
    ingest_document(user_id, str(p), course_name="过滤课", title="C10")
    assert [d["title"] for d in list_documents(user_id, "过滤课")] == ["C10"]
    assert list_documents(user_id, "不存在的课") == []
