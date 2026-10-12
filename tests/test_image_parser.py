"""A. 图片解析器（OCR）测试：等价类 + 边界值 + mock 回归"""

import pytest

from wisnote.parsers import parse_file
from wisnote.parsers.image_parser import _join, parse_image


# ---------- 真实 OCR（引擎真实加载，验证端到端识别） ----------

def test_real_ocr_printed_text(make_text_image):
    """A1 有效类：打印体中文图片 -> 识别出预期内容"""
    img = make_text_image("t1.png", [(40, "操作系统课程笔记")])
    blocks = parse_image(img)
    assert blocks, "应至少识别出一个段落"
    joined = " ".join(b.text for b in blocks)
    assert "操作系统" in joined, f"OCR 内容不符: {joined}"


def test_real_ocr_two_paragraphs(make_text_image):
    """A2 有效类：垂直大间距的两行 -> 按间距聚合为 2 个段落"""
    img = make_text_image("t2.png", [(40, "页面置换算法"), (200, "LRU 最近最久未使用")])
    blocks = parse_image(img)
    assert len(blocks) == 2, f"应聚合为 2 段，实际 {len(blocks)}: {[b.text for b in blocks]}"
    assert "页面置换" in blocks[0].text
    assert "LRU" in blocks[1].text


def test_real_ocr_blank_image(tmp_path):
    """A3 无效类：空白图片 -> 返回空列表（降级信号）"""
    from PIL import Image

    p = tmp_path / "blank.png"
    Image.new("RGB", (320, 160), "white").save(p)
    assert parse_image(p) == []


def test_parse_file_dispatch_image(tmp_path):
    """A3b 分发：parse_file 按扩展名路由到图片解析器"""
    from PIL import Image

    p = tmp_path / "blank.bmp"
    Image.new("RGB", (320, 160), "white").save(p)
    assert parse_file(p) == []


# ---------- mock OCR（确定性单元测试） ----------

def _line(text: str, score, top: float = 10.0, height: float = 30.0) -> list:
    """构造 RapidOCR 返回格式的单行结果"""
    box = [[10.0, top], [300.0, top], [300.0, top + height], [10.0, top + height]]
    return [box, text, score]


@pytest.fixture()
def patch_ocr(monkeypatch):
    """替换 OCR 引擎为假实现，返回可设置的容器"""

    holder = {"result": []}

    class _FakeOCR:
        def __call__(self, path):
            if isinstance(holder["result"], Exception):
                raise holder["result"]
            return holder["result"], 0.05

    import wisnote.parsers.image_parser as ip

    monkeypatch.setattr(ip, "_get_ocr", lambda: _FakeOCR())
    return holder


def test_ocr_engine_exception_returns_empty(patch_ocr, tmp_path):
    """A4 异常路径：OCR 引擎抛错 -> 返回空而非崩溃"""
    patch_ocr["result"] = RuntimeError("engine boom")
    assert parse_image(tmp_path / "x.png") == []


def test_low_score_filtered(patch_ocr, tmp_path):
    """A5 边界外：置信度 0.3 < 0.5 -> 丢弃；0.5 恰好保留（边界值）"""
    patch_ocr["result"] = [_line("低置信行", "0.3"), _line("边界行", "0.5")]
    blocks = parse_image(tmp_path / "x.png")
    assert [b.text for b in blocks] == ["边界行"], f"应只保留 score>=0.5: {blocks}"


def test_string_score_compat(patch_ocr, tmp_path):
    """A6 回归：RapidOCR 1.2.x 的 score 是字符串 -> 必须被接受（真实抓到过的 bug）"""
    patch_ocr["result"] = [_line("字符串分数行", "0.9")]
    blocks = parse_image(tmp_path / "x.png")
    assert [b.text for b in blocks] == ["字符串分数行"]


def test_empty_text_and_none_score_dropped(patch_ocr, tmp_path):
    """A7 无效类：空文本 / score=None -> 丢弃"""
    patch_ocr["result"] = [_line("", "0.9"), _line("无分数行", None)]
    assert parse_image(tmp_path / "x.png") == []


# ---------- 纯函数单元 ----------

def test_join_cjk_no_space_latin_space():
    """A8 拼接规则：中文间不留空格，西文间补空格"""
    assert _join(["操作系统", "课程"]) == "操作系统课程"
    assert _join(["hello", "world"]) == "hello world"
    assert _join(["版本", "v2"]) == "版本v2"
