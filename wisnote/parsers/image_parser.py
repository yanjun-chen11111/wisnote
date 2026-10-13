"""图片解析器：OCR 提取文字，让图片与文本资料走同一条检索流水线

采用 RapidOCR（PaddleOCR 模型的 ONNX 轻量版，纯 pip 安装、CPU 推理）。
- 打印体 / 截图 / 教材照片效果好；手写笔记识别率有限。
- 识别不到文字时返回空列表，由入库层降级为"仅存档预览"。
- ocr_image_bytes()：供 PDF/PPT 的"渲染成图再 OCR"复用（不落盘）。
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


def _lines_to_blocks(lines: list[tuple], page: int) -> list[Block]:
    """OCR 行列表（box/text/score）→ 按垂直间距聚合的段落 Block 列表"""
    paras: list[str] = []
    cur: list[str] = []
    prev_bottom = None
    prev_h = 0.0

    for item in lines:
        text = (item.get("text") or "").strip() if isinstance(item, dict) else (item[1] or "").strip()
        try:
            score = float(item.get("score") if isinstance(item, dict) else item[2])
        except (TypeError, ValueError):
            continue
        box = item.get("box") if isinstance(item, dict) else item[0]
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

    return [Block(page=page, para_index=i, text=t) for i, t in enumerate(paras, start=1) if t]


def ocr_image_bytes(img_bytes: bytes, page: int = 1) -> list[Block]:
    """OCR 内存图片（PNG/JPG 字节），供 PDF/PPT 渲染 fallback 使用"""
    import numpy as np
    from PIL import Image

    img = Image.open(__import__("io").BytesIO(img_bytes))
    arr = np.array(img)
    if arr.ndim == 3 and arr.shape[2] == 4:  # RGBA → RGB（部分 OCR 输入不吃 4 通道）
        arr = np.array(img.convert("RGB"))
    result, _ = _get_ocr()(arr)
    if not result:
        return []
    return _lines_to_blocks(result, page)


def parse_image(path: Path) -> list[Block]:
    """OCR 识别图片文字，按垂直间距聚合成段落；无文字或识别失败返回 []"""
    try:
        result, _ = _get_ocr()(str(path))
    except Exception:
        return []
    if not result:
        return []
    return _lines_to_blocks(result, page=1)
