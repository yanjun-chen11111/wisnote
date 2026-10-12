"""E. 讲解依据路由测试：决策表（source × 有无资料），mock 检索与模型"""

import pytest

import wisnote.explain as ex
from wisnote.explain import NO_MATERIAL, stream_explain


@pytest.fixture()
def mocks(monkeypatch):
    """替换 search_notes 与 chat_stream，记录调用"""
    calls = {"search": [], "llm": []}

    def fake_search(user_id, query, top_k=5):
        calls["search"].append(query)
        return calls.get("search_results", [])

    def fake_stream(messages, options=None):
        calls["llm"].append(messages)
        yield "模型讲解内容。"

    monkeypatch.setattr(ex, "search_notes", fake_search)
    monkeypatch.setattr(ex, "chat_stream", fake_stream)
    return calls


def _hit(doc_id: int, similarity: float = 0.8) -> dict:
    return {
        "document_id": doc_id,
        "title": "测试文档",
        "text": "相关笔记片段内容",
        "page": 1,
        "para_index": 2,
        "similarity": similarity,
    }


def _run(user_id, topic, source, holder=None):
    return "".join(
        stream_explain(user_id, topic, mode="plain", save=False,
                       result_holder=holder, source=source)
    )


# ---------- 参数校验（无效类） ----------

def test_invalid_source(user_id, mocks):
    with pytest.raises(ValueError, match="auto / notes / llm"):
        _run(user_id, "主题", "wildcard")


def test_invalid_mode(user_id, mocks):
    with pytest.raises(ValueError, match="未知讲解模式"):
        "".join(stream_explain(user_id, "主题", mode="nope", save=False, source="auto"))


def test_empty_topic(user_id, mocks):
    with pytest.raises(ValueError, match="不能为空"):
        _run(user_id, "   ", "auto")


# ---------- 决策表：source × has_material ----------

def test_notes_no_material_refuses(mocks, user_id):
    """E4 notes×无资料 -> 精确拒答文案，且不调用模型"""
    h = {}
    text = _run(user_id, "量子纠缠", "notes", h)
    assert text == NO_MATERIAL.format("量子纠缠")
    assert mocks["llm"] == [], "严格模式缺资料时不得调用模型"
    assert h["sources"] == [] and h["grounded"] is False


def test_auto_no_material_degrades_with_prefix(mocks, user_id):
    """E5 auto×无资料 -> ℹ️ 降级前缀 + 调用模型（无来源）"""
    h = {}
    text = _run(user_id, "量子纠缠", "auto", h)
    assert text.startswith("ℹ️ 笔记中未找到相关内容"), text[:40]
    assert "模型讲解内容" in text
    assert len(mocks["llm"]) == 1
    assert h["sources"] == [] and h["grounded"] is False


def test_llm_skips_search(mocks, user_id):
    """E6 llm -> 完全不检索，直接模型讲解"""
    h = {}
    text = _run(user_id, "任意主题", "llm", h)
    assert mocks["search"] == [], "llm 档不得检索笔记"
    assert "模型讲解内容" in text
    assert h["grounded"] is False


def test_notes_with_material_grounded(mocks, user_id, tmp_path):
    """E7 notes×有资料 -> grounded 提示词（含笔记片段与来源编号）+ 引用计数+1"""
    from wisnote.db import get_conn
    from wisnote.ingest import ingest_document

    p = tmp_path / "e7.txt"
    p.write_text("引用计数测试文档内容。" * 5, encoding="utf-8")
    doc_id = ingest_document(user_id, str(p), title="E7")["document_id"]

    conn = get_conn()
    before = conn.execute(
        "SELECT ref_count FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()["ref_count"]
    conn.close()

    mocks["search_results"] = [_hit(doc_id)]
    h = {}
    text = _run(user_id, "LRU", "notes", h)

    assert "模型讲解内容" in text
    assert mocks["llm"], "应调用模型"
    messages = mocks["llm"][0]
    assert "笔记片段" in messages[1]["content"], "用户消息应包含笔记片段"
    assert "来源编号" in messages[1]["content"], "应要求标注来源编号"
    assert h["grounded"] is True and len(h["sources"]) == 1

    conn = get_conn()
    after = conn.execute(
        "SELECT ref_count FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()["ref_count"]
    conn.close()
    assert after == before + 1, f"引用计数应 +1（{before} -> {after}）"


def test_auto_with_material_no_prefix(mocks, user_id, tmp_path):
    """E8 auto×有资料 -> 走笔记（grounded），且不带降级前缀"""
    mocks["search_results"] = [_hit(999)]  # 引用计数对不存在文档静默无效，不影响断言
    h = {}
    text = _run(user_id, "LRU", "auto", h)
    assert not text.startswith("ℹ️"), "有资料时不得出现降级前缀"
    assert h["grounded"] is True
    assert "笔记片段" in mocks["llm"][0][1]["content"]


def test_low_similarity_treated_as_no_material(mocks, user_id):
    """E9 边界：相似度 0.29 < 0.30 阈值 -> 视为无资料（notes 拒答）"""
    mocks["search_results"] = [_hit(1, similarity=0.29)]
    h = {}
    text = _run(user_id, "主题", "notes", h)
    assert text == NO_MATERIAL.format("主题")
    assert mocks["llm"] == []


def test_similarity_threshold_inclusive(mocks, user_id):
    """E10 边界：相似度恰为 0.30 -> 视为有资料（grounded）"""
    mocks["search_results"] = [_hit(999, similarity=0.30)]
    h = {}
    _run(user_id, "主题", "auto", h)
    assert h["grounded"] is True, "0.30 应含在阈值内（>=）"
