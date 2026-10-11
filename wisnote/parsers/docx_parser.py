"""Word 解析器：提取正文段落与表格文字

说明：.docx 格式本身不保存"页码"信息（分页由渲染决定），
因此 page 为 None，溯源时定位到"段落序号 + 原文片段"。
"""

from pathlib import Path

from docx import Document

from wisnote.parsers.base import Block


def parse_docx(path: Path) -> list[Block]:
    blocks: list[Block] = []
    para = 0
    doc = Document(str(path))
    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            para += 1
            blocks.append(Block(page=None, para_index=para, text=t))
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                t = cell.text.strip()
                if t:
                    para += 1
                    blocks.append(Block(page=None, para_index=para, text=t))
    return blocks
