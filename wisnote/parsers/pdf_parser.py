"""PDF 解析器：逐页提取文本块，保留页码 + 页内顺序

两层策略（业界常规 OCR fallback）：
1. 电子层：PyMuPDF 直接读文本块（Word/LaTeX 导出的电子版 PDF，零损耗）；
2. OCR fallback：整份提取为空 → 判定为扫描版/拍照版，逐页渲染成位图
   走 RapidOCR（纯图片 PDF 是课件场景的常见形态）。

注意：只做"整份为空才 fallback"，不做逐页混合——逐页判定在
"目录页无字+正文有字"的混合 PDF 上会误触发，且绝大多数 PDF 是纯电子/纯扫描。
"""

from pathlib import Path

import pymupdf  # PyMuPDF

from wisnote.parsers.base import Block

# 渲染分辨率：150 DPI 对打印体 OCR 足够，比 300 快 4 倍
RENDER_DPI = 150
# PDF 页数上限保护：超大声称扫描件的 PDF 逐页 OCR 会非常慢，先拒绝
MAX_OCR_PAGES = 200


def _extract_text_layer(page) -> list[str]:
    """按阅读顺序提取一页的文本块（type 0 为文本块）"""
    texts = []
    for b in page.get_text("blocks", sort=True):
        text, block_type = b[4], b[6]
        if block_type == 0 and text.strip():
            texts.append(text.strip())
    return texts


def parse_pdf(path: Path) -> list[Block]:
    blocks: list[Block] = []
    para = 0
    with pymupdf.open(path) as doc:
        page_texts: list[list[str]] = [_extract_text_layer(p) for p in doc]

        # OCR fallback：整份电子层为空 → 扫描版，逐页渲染识别
        if not any(page_texts):
            n_pages = len(doc)
            if n_pages > MAX_OCR_PAGES:
                raise ValueError(
                    f"扫描版 PDF 共 {n_pages} 页，超过 OCR 上限 {MAX_OCR_PAGES} 页，"
                    "请拆分后再上传"
                )
            from wisnote.parsers.image_parser import ocr_image_bytes

            for page_no, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=RENDER_DPI)
                for b in ocr_image_bytes(pix.tobytes("png"), page=page_no):
                    para += 1
                    blocks.append(Block(page=page_no, para_index=para, text=b.text))
            return blocks

        # 电子层路径（原逻辑）
        for page_no, texts in enumerate(page_texts, start=1):
            for t in texts:
                para += 1
                blocks.append(Block(page=page_no, para_index=para, text=t))
    return blocks
