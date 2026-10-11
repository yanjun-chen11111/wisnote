"""文档解析入口：根据扩展名分发到对应解析器"""

from pathlib import Path

from wisnote.parsers.base import Block
from wisnote.parsers.docx_parser import parse_docx
from wisnote.parsers.pdf_parser import parse_pdf
from wisnote.parsers.pptx_parser import parse_pptx
from wisnote.parsers.txt_parser import parse_txt

__all__ = ["Block", "parse_file", "SUPPORTED"]

SUPPORTED = {
    ".pdf": parse_pdf,
    ".pptx": parse_pptx,
    ".docx": parse_docx,
    ".txt": parse_txt,
    ".md": parse_txt,
}


def parse_file(path: Path | str) -> list[Block]:
    """解析文档，返回带位置元数据的段落列表"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    parser = SUPPORTED.get(path.suffix.lower())
    if parser is None:
        raise ValueError(
            f"暂不支持的文件类型「{path.suffix}」，当前支持：{'/'.join(SUPPORTED)}"
        )
    return parser(path)
