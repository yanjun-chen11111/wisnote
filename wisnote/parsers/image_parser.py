"""图片解析器：OCR 提取文字，让图片与文本资料走同一条检索流水线

采用 RapidOCR（PaddleOCR 模型的 ONNX 轻量版，纯 pip 安装、CPU 推理）。
- 打印体 / 截图 / 教材照片效果好；手写笔记识别率有限。
- 识别不到文字时返回空列表，由入库层降级为"仅存档预览"。
"""

from pathlib import Path

from wisnote.parsers.base import Block

# 置信度低于该值的文本行丢弃（RapidOCR 返回 0~1 置信度）
MIN_SCORE = 0.5
# 行间空白超过前一行的 1.2 倍行高，视为换段
PARA_GAP_RATIO = 1.2

_ocr = None


def _get_ocr():
    """懒加载 OCR 模型（首次调用加载，之后复用）"""
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _join(lines: list[str]) -> str:
    """段内行拼接：中文之间不留空格，西文之间补空格"""
    out = ""
    for ln in lines:
        if out and not _is_cjk(out[-1]) and not _is_cjk(ln[0]):
            out += " "
        out += ln
    return out


def parse_image(path: Path) -> list[Block]:
    """OCR 识别图片文字，按垂直间距聚合成段落；无文字或识别失败返回 []"""
    try:
        result, _ = _get_ocr()(str(path))
    except Exception:
        return []
    if not result:
        return []

    paras: list[str] = []
    cur: list[str] = []
    prev_bottom = None
    prev_h = 0.0

    for item in result:
        # 兼容不同版本返回格式：[box, text, score] 或 dict
        if isinstance(item, dict):
            box, text, score = item.get("box"), item.get("text"), item.get("score")
        else:
            box, text, score = item[0], item[1], item[2]
        text = (text or "").strip()
        # 某些版本 score 返回字符串，统一转 float
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if not text or score < MIN_SCORE:
            continue
        if not box or len(box) < 2:
            continue
        ys = [p[1] for p in box]
        top, bottom = min(ys), max(ys)
        if cur and prev_bottom is not None and (top - prev_bottom) > PARA_GAP_RATIO * max(prev_h, 1.0):
            paras.append(_join(cur))
            cur = []
        cur.append(text)
        prev_bottom = bottom
        prev_h = bottom - top

    if cur:
        paras.append(_join(cur))

    return [Block(page=1, para_index=i, text=t) for i, t in enumerate(paras, start=1) if t]
