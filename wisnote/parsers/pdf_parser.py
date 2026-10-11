"""PDF 解析器：逐页提取文本块，保留页码 + 页内顺序"""

from pathlib import Path

import pymupdf  # PyMuPDF

from wisnote.parsers.base import Block


def parse_pdf(path: Path) -> list[Block]:
    blocks: list[Block] = []
    para = 0
    with pymupdf.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            # "blocks" 模式按布局返回文本块；sort=True 按阅读顺序（从上到下）排序
            raw_blocks = page.get_text("blocks", sort=True)
            for b in raw_blocks:
                # 结构：(x0, y0, x1, y1, text, block_no, block_type)；type 0 为文本块
                text, block_type = b[4], b[6]
                if block_type != 0:
                    continue
                text = text.strip()
                if not text:
                    continue
                para += 1
                blocks.append(Block(page=page_no, para_index=para, text=text))
    return blocks
