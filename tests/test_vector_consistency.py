"""向量库与 SQLite 一致性测试：删除同步 + 僵尸清理 + 查询过滤"""


from wisnote.ingest import delete_document, ingest_document
from wisnote.search import search_notes
from wisnote.vectorstore import get_store


def _txt(tmp_path, name: str, marker: str) -> object:
    p = tmp_path / name
    p.write_text(f"{marker} 是本测试的独有锚点内容。" * 5, encoding="utf-8")
    return p


def test_delete_syncs_vector_store(user_id, tmp_path):
    """V1 删除文档 → 向量库同步清空（锁住同步行为，防僵尸再产生）"""
    p = _txt(tmp_path, "v1.txt", "僵尸同步验证甲锚点")
    r = ingest_document(user_id, str(p), title="V1")
    col = get_store().collection(user_id)
    assert col.count() == r["chunks"] > 0, "入库后向量数应与块数一致"

    delete_document(user_id, r["document_id"])
    assert col.count() == 0, "删除后向量库必须同步清空"


def test_purge_orphans(user_id, tmp_path):
    """V2 手造僵尸向量 → purge_orphans 清理，且只清僵尸"""
    p1 = _txt(tmp_path, "a.txt", "孤儿清理验证锚点一")
    p2 = _txt(tmp_path, "b.txt", "孤儿清理验证锚点二")
    r1 = ingest_document(user_id, str(p1), title="A")
    r2 = ingest_document(user_id, str(p2), title="B")

    # 直接从 SQLite 删除 B（绕过 ingest 的同步删除，模拟旧版 bug 产生僵尸）
    from wisnote.db import get_conn

    conn = get_conn()
    try:
        conn.execute("DELETE FROM documents WHERE id=?", (r2["document_id"],))
        conn.commit()
    finally:
        conn.close()

    store = get_store()
    n = store.purge_orphans(user_id, {r1["document_id"]})
    assert n == r2["chunks"], f"应清掉 B 的 {r2['chunks']} 条僵尸，实际 {n}"
    hits = store.search(user_id, "孤儿清理验证锚点二", 3)
    # A 与 B 文本同模板、语义相近，A 的块命中是正常的；
    # 验收点是 B 的原文不再出现在任何命中里
    assert not any("锚点二" in h["text"] for h in hits), "僵尸清理后检索不到已删文档内容"


def test_search_filters_zombie_hits(user_id, tmp_path):
    """V3 查询时防御过滤：即使有僵尸（未清理），也进不了检索结果"""
    p = _txt(tmp_path, "c.txt", "防御过滤验证锚点三")
    r = ingest_document(user_id, str(p), title="C")

    from wisnote.db import get_conn

    conn = get_conn()
    try:
        conn.execute("DELETE FROM documents WHERE id=?", (r["document_id"],))
        conn.commit()
    finally:
        conn.close()

    hits = search_notes(user_id, "防御过滤验证锚点三", 5)
    assert hits == [], "指向已删文档的命中必须被过滤掉，不得以'未知文档'出现"
