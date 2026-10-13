"""回归测试：stream_ask 带 session_id 时的 UnboundLocalError（静态分析 ruff F823 抓到的真实缺陷）

背景：stream_ask 函数体内局部导入过 get_messages（用于长对话压缩），
Python 因此把它视为函数局部变量，导致 281 行在局部导入执行前引用它时抛
UnboundLocalError。free/auto/notes 命中路径全部受影响。修复：删除重复的
局部导入，仅用模块级导入。
"""

import pytest

import wisnote.rag as rag
from wisnote.chat import create_session, get_messages


@pytest.fixture()
def mock_llm(monkeypatch):
    """替掉模型调用与检索（不依赖 Ollama / 向量库）"""

    def fake_search(user_id, query, top_k=5):
        return []

    def fake_stream(messages, options=None):
        yield "测试回答。"

    monkeypatch.setattr(rag, "search_notes", fake_search)
    monkeypatch.setattr(rag, "chat_stream", fake_stream)
    monkeypatch.setattr(rag, "rewrite_query", lambda q, sid: q)


def test_stream_ask_free_mode_with_session_no_crash(user_id, mock_llm):
    """R1 回归：free 模式 + session_id -> 不抛 UnboundLocalError，正常产出并存档"""
    sid = create_session(user_id, "回归会话")
    holder = {}
    out = list(
        rag.stream_ask(user_id, "测试问题", mode="free", session_id=sid,
                       result_holder=holder, use_history=False)
    )
    assert out and "测试回答" in "".join(out)
    assert holder.get("mode") == "free"
    # 存档：第一轮结束后应有用户提问与 AI 回答两条消息（auto_name 在此 mock 下安全跳过失败）
    msgs = get_messages(sid)
    assert any(m["role"] == "user" for m in msgs)


def test_stream_ask_notes_mode_with_hits_no_crash(user_id, mock_llm, monkeypatch):
    """R2 回归：notes 模式命中资料 -> 走到 281 行之后的路径也不崩"""

    def fake_search_hits(user_id, query, top_k=5):
        return [{
            "document_id": None, "title": "测试文档", "text": "片段内容",
            "page": 1, "para_index": 1, "similarity": 0.9,
        }]

    monkeypatch.setattr(rag, "search_notes", fake_search_hits)
    sid = create_session(user_id, "回归会话2")
    holder = {}
    out = list(
        rag.stream_ask(user_id, "页面置换", mode="notes", session_id=sid,
                       result_holder=holder, use_history=False)
    )
    assert out
    assert holder.get("mode") == "notes"
    assert len(holder.get("sources", [])) == 1
