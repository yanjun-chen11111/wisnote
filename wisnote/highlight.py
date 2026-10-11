"""PDF 原文高亮预览（溯源最后一公里）

把溯源定位从"文件 + 页码 + 段落"推进到"原文页面截图 + 命中文字高亮"：
1. 用 PyMuPDF 打开原 PDF，渲染指定页为图片；
2. 用检索片段在该页做文本搜索，命中区域加高亮批注；
3. 支持"整页视图"与"命中区域放大视图"（自动裁剪到命中处附近）。

仅 PDF 支持（Word/文本没有固定页码，退回段落级溯源）。
"""

import re
from pathlib import Path

import pymupdf

from wisnote.db import get_conn

HIGHLIGHT_COLOR = (1, 0.95, 0.4)   # 淡黄高亮


def get_document_info(document_id: int) -> dict | None:
    """取资料的文件路径与类型（供渲染用）"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, title, file_path, file_type FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def can_highlight(file_type: str) -> bool:
    return (file_type or "").lower() == "pdf"


def split_segments(text: str) -> list[str]:
    """把片段切成可搜索的小句，长句优先（提高命中率）"""
    parts = re.split(r"[\n。！？；;]+", text or "")
    segs = [p.strip() for p in parts if len(p.strip()) >= 6]
    return sorted(segs, key=len, reverse=True)


def find_rects(page, snippet: str) -> tuple[str | None, list]:
    """在页面中定位片段，返回 (命中的文本, 矩形列表)"""
    snippet = (snippet or "").strip()
    if not snippet:
        return None, []
    # 1) 按句子匹配（长句优先）
    for seg in split_segments(snippet):
        rects = page.search_for(seg)
        if rects:
            return seg, rects
    # 2) 退化为前缀滑窗匹配
    for n in (20, 15, 12, 10, 8, 6):
        if len(snippet) < n:
            continue
        rects = page.search_for(snippet[:n])
        if rects:
            return snippet[:n], rects
    return None, []


def render_pdf_page(
    file_path: str | Path,
    page_no: int,
    snippet: str | None = None,
    dpi: int = 110,
    zoom: bool = False,
    margin: int = 60,
) -> dict:
    """渲染 PDF 指定页，并高亮命中文字

    返回：{"image": PNG字节, "matched": 命中的文本或None, "page": 页码, "zoomed": 是否放大视图}
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"原文件已丢失：{path}")

    doc = pymupdf.open(path)
    try:
        if page_no < 1 or page_no > doc.page_count:
            raise ValueError(f"页码超出范围（共 {doc.page_count} 页）")
        page = doc[page_no - 1]

        matched, rects = find_rects(page, snippet or "")
        for r in rects:
            annot = page.add_highlight_annot(r)
            annot.set_colors(stroke=HIGHLIGHT_COLOR)
            annot.update()

        clip = None
        if zoom and rects:
            x0 = max(0, min(r.x0 for r in rects) - margin)
            y0 = max(0, min(r.y0 for r in rects) - margin)
            x1 = min(page.rect.width, max(r.x1 for r in rects) + margin)
            y1 = min(page.rect.height, max(r.y1 for r in rects) + margin)
            clip = pymupdf.Rect(x0, y0, x1, y1)

        pix = page.get_pixmap(dpi=dpi, clip=clip, annots=True)
        return {
            "image": pix.tobytes("png"),
            "matched": matched,
            "page": page_no,
            "zoomed": bool(zoom and rects),
        }
    finally:
        doc.close()   # 不保存，高亮批注只存在于内存中


def render_document_page(
    document_id: int,
    page_no: int,
    snippet: str | None = None,
    zoom: bool = False,
) -> dict:
    """按资料ID渲染（内部先查文件路径与类型）"""
    info = get_document_info(document_id)
    if info is None:
        raise ValueError("资料不存在")
    if not can_highlight(info["file_type"]):
        raise ValueError(f"{info['file_type'].upper()} 不支持原文高亮预览（仅 PDF 支持）")
    return render_pdf_page(info["file_path"], page_no, snippet, zoom=zoom)
