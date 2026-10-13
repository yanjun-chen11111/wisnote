"""引用位置增强测试：确定性替换 + 跨 chunk 分裂编号 + 零幻觉保证

背景：让 1.7B 模型自己写引用位置会编造页码（E2E 实测：真实"无页码 第1段"
被写成"第12页 第3段"），改为程序确定性替换后必须有测试锁住该行为。
"""

from wisnote.rag import _enrich_stream, cite_label, enrich_citations

SOURCES = [
    {"index": 1, "title": "操作系统笔记", "page": 12, "para_index": 3, "similarity": 0.6},
    {"index": 2, "title": "计算机网络", "page": None, "para_index": 6, "similarity": 0.5},
]


def test_enrich_basic():
    """C1 [n] 被替换为真实位置；无页码文档显示'无页码'"""
    out = enrich_citations("死锁的四个条件[1]与UDP[2]。", SOURCES)
    assert "[1]《操作系统笔记》第12页 第3段" in out
    assert "[2]《计算机网络》无页码 第6段" in out


def test_enrich_unknown_index_passthrough():
    """C2 模型编出的未知编号原样保留（不崩、不瞎补）"""
    out = enrich_citations("内容[3]。", SOURCES)
    assert "[3]" in out and "《" not in out


def test_enrich_all_occurrences():
    """C3 同一编号出现多次都替换"""
    out = enrich_citations("A[1] B[1]", SOURCES)
    assert out.count("《操作系统笔记》第12页 第3段") == 2


def test_cite_label_format():
    """C4 标签格式：有页码带页码，段落号可选"""
    assert cite_label(SOURCES[0]) == "[1]《操作系统笔记》第12页 第3段"
    assert cite_label(SOURCES[1]) == "[2]《计算机网络》无页码 第6段"


def test_stream_enrich_split_marker():
    """S1 编号拆在 chunk 交界（'[' + '1' + ']'）仍正确替换"""
    chunks = ["死锁条件", "[", "1] 很重要", "。"]
    out = "".join(_enrich_stream(iter(chunks), SOURCES))
    assert "[1]《操作系统笔记》第12页 第3段" in out
    assert out.endswith("很重要。")


def test_stream_enrich_multiple_and_incremental():
    """S2 流式：多个编号都替换；每个编号只消费一次（无重复、无遗漏）"""
    chunks = ["条件[1]展开", "，策略[2]如下。"]
    got = list(_enrich_stream(iter(chunks), SOURCES))
    joined = "".join(got)
    assert "[1]《操作系统笔记》第12页 第3段" in joined
    assert "[2]《计算机网络》无页码 第6段" in joined
    assert joined == "条件[1]《操作系统笔记》第12页 第3段展开，策略[2]《计算机网络》无页码 第6段如下。"
    assert len(got) >= 3, "应分多次 yield（体现流式），而非一次性吐出"


def test_stream_enrich_citation_at_end():
    """S3 编号在流的末尾：结束时也要替换（不留半截）"""
    chunks = ["结论一句话[1]"]
    out = "".join(_enrich_stream(iter(chunks), SOURCES))
    assert out == "结论一句话[1]《操作系统笔记》第12页 第3段"


def test_stream_enrich_no_sources_passthrough():
    """S4 自由模式（无来源）：流式原样透传"""
    out = "".join(_enrich_stream(iter(["普通回答[1]"]), []))
    assert out == "普通回答[1]"
