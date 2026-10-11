"""纯文本 / Markdown 解析器：按空行分段"""

import re
from pathlib import Path

from wisnote.parsers.base import Block


def _read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别文件编码：{path.name}")


def parse_txt(path: Path) -> list[Block]:
    text = _read_text(path)
    # 优先按空行分段
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    # 整个文件没有空行时，退化为按行分段
    if len(paras) <= 1 and "\n" in text:
        paras = [line.strip() for line in text.splitlines() if line.strip()]
    return [Block(page=None, para_index=i, text=t) for i, t in enumerate(paras, start=1)]
