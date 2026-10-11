"""PPT 解析器：逐页（幻灯片）提取文本框/表格文字，保留页码"""

from pathlib import Path

from pptx import Presentation

from wisnote.parsers.base import Block


def parse_pptx(path: Path) -> list[Block]:
    blocks: list[Block] = []
    para = 0
    prs = Presentation(str(path))
    for slide_no, slide in enumerate(prs.slides, start=1):
        texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in p.runs).strip()
                    if t:
                        texts.append(t)
            elif getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    for cell in row.cells:
                        t = cell.text.strip()
                        if t:
                            texts.append(t)
        for t in texts:
            para += 1
            blocks.append(Block(page=slide_no, para_index=para, text=t))
    return blocks
