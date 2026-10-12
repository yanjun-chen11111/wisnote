"""D. 资料替换测试：状态迁移（文本→文本 / 文本→图片 / 不存在）"""

from pathlib import Path

import pytest

from wisnote.ingest import ingest_document, list_documents, update_document


def _doc(docs, title):
    return next(d for d in docs if d["title"] == title)


def test_replace_text_with_text(user_id, tmp_path):
    """D1 迁移：文本→文本 -> 新切块生效、旧文件删除、新文件存在"""
    old = tmp_path / "old.txt"
    old.write_text("旧版本内容，用于被替换。" * 5, encoding="utf-8")
    r1 = ingest_document(user_id, str(old), title="D1")
    old_path = _doc(list_documents(user_id), "D1")["file_path"]

    new = tmp_path / "new.txt"
    new.write_text("新版本内容，替换后的文本，应当被重新切块入库。" * 5, encoding="utf-8")
    r2 = update_document(user_id, r1["document_id"], str(new))

    assert r2["chunks"] > 0
    doc = _doc(list_documents(user_id), "D1")
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == r2["chunks"]
    assert not Path(old_path).exists(), "旧文件应被删除"
    assert Path(doc["file_path"]).exists(), "新文件应存在"


def test_replace_text_with_image_zeroes_chunks(user_id, tmp_path, make_text_image):
    """D2 迁移：文本→无字图片 -> chunk_count 归零（检索退出、存档保留）"""
    old = tmp_path / "old.txt"
    old.write_text("将要被图片替换的文本内容。" * 5, encoding="utf-8")
    r1 = ingest_document(user_id, str(old), title="D2")
    assert r1["chunks"] > 0

    from PIL import Image

    img = tmp_path / "blank.png"
    Image.new("RGB", (320, 160), "white").save(img)
    update_document(user_id, r1["document_id"], str(img))

    doc = _doc(list_documents(user_id), "D2")
    assert doc["chunk_count"] == 0, "替换为无字图片后应无切块"
    assert doc["status"] == "ready"


def test_replace_nonexistent_document(user_id, tmp_path):
    """D3 非法迁移：不存在的文档 -> ValueError"""
    p = tmp_path / "x.txt"
    p.write_text("内容", encoding="utf-8")
    with pytest.raises(ValueError, match="不属于当前用户"):
        update_document(user_id, 999999, str(p))
