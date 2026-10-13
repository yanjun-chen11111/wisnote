"""llm._clean_stream 单元测试：流式透传 + think 过滤 + 跨 chunk 分裂标记

核心回归点（真实事故）：无 think 标记的常态下内容必须逐段透传，
不许攒到最后一次性 yield（该 bug 曾让"流式输出"从未生效，E2E 实测 chunk数=1）。
"""

from wisnote.llm import _THINK_RE, _clean_stream

OPEN, _, CLOSE = _THINK_RE.pattern.partition(".*?")


def _run(chunks: list[str]) -> tuple[list[str], str]:
    out = list(_clean_stream(iter(chunks)))
    return out, "".join(out)


def test_plain_content_streams_incrementally():
    """S1 无标记：多 chunk 输入必须产出多个 yield（逐段透传），内容完整"""
    chunks = ["死锁的", "四个", "必要条件是：", "互斥、", "请求保持、", "不可剥夺、", "循环等待。"]
    out, joined = _run(chunks)
    assert len(out) >= 4, f"必须逐段透传，实际只 yield 了 {len(out)} 次"
    assert joined == "".join(chunks)


def test_think_block_filtered_answer_streamed():
    """S2 think 块被过滤，答案逐段透传"""
    chunks = [OPEN, "推理过程应该被", "过滤掉", CLOSE, "答案是", "死锁。"]
    out, joined = _run(chunks)
    assert "推理过程" not in joined
    assert "过滤掉" not in joined
    assert joined.strip() == "答案是死锁。", f"实际: {joined!r}"
    assert len(out) >= 1


def test_split_marker_across_chunks():
    """S3 标记被拆在 chunk 交界仍正确识别（防分裂尾巴的职责）"""
    # OPEN 从中间劈开
    mid = len(OPEN) // 2
    chunks = [OPEN[:mid], OPEN[mid:] + "思考内容", CLOSE, "正文"]
    joined = "".join(_clean_stream(iter(chunks)))
    assert joined == "正文", f"思考内容应被过滤，实际: {joined!r}"


def test_split_close_marker_across_chunks():
    """S4 闭标记被拆开仍正确恢复正文"""
    mid = len(CLOSE) // 2
    chunks = [OPEN + "think", CLOSE[:mid], CLOSE[mid:] + "回答", "内容。"]
    joined = "".join(_clean_stream(iter(chunks)))
    assert "回答" in joined
    assert "think" not in joined


def test_unclosed_think_yields_nothing():
    """S5 think 未闭合：全部内容被过滤（不半吐）"""
    out, joined = _run([OPEN + "只思考不回答"])
    assert joined == ""


def test_single_big_chunk_passthrough():
    """S6 单个大 chunk：完整透传一次"""
    out, joined = _run(["一整段完整回答"])
    assert joined == "一整段完整回答"
