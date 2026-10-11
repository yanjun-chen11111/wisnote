"""文本切块：把段落组装成适合 Embedding 的块，保留溯源位置元数据

策略：
1. 超过 CHUNK_SIZE 的长段落，用 RecursiveCharacterTextSplitter 按语义边界二次切分
   （该段落所有子片段继承同一 page/para_index，溯源仍可定位）；
2. 普通段落按顺序组装，累计不超过 CHUNK_SIZE；
3. 相邻块之间保留末段重叠（块级重叠），减少语义被切断的问题；
4. 每个块记录起始段落的 page/para_index，供前端"跳转原文"使用。
"""

from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from wisnote.config import CHUNK_SIZE, CHUNK_OVERLAP
from wisnote.parsers import Block


@dataclass
class Chunk:
    chunk_index: int            # 文档内序号（0 起）
    text: str
    page: int | None            # 起始段落所在页码（docx/txt 为 None）
    para_index: int             # 起始段落序号
    blocks: list[Block] = field(default_factory=list)


_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def _split_long_blocks(blocks: list[Block], chunk_size: int, overlap: int) -> list[Block]:
    """把超长段落切成 ≤ chunk_size 的片段，位置元数据不变"""
    pieces: list[Block] = []
    for b in blocks:
        if len(b.text) <= chunk_size:
            pieces.append(b)
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
                separators=_SEPARATORS,
            )
            for t in splitter.split_text(b.text):
                pieces.append(Block(page=b.page, para_index=b.para_index, text=t))
    return pieces


def chunk_blocks(
    blocks: list[Block],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    """把解析出的段落组装成块列表（参数可覆盖，供寻优实验用）"""
    if not blocks:
        return []
    pieces = _split_long_blocks(blocks, chunk_size, overlap)

    chunks: list[Chunk] = []
    buf: list[Block] = []
    total = 0

    def flush() -> None:
        nonlocal buf, total
        chunks.append(
            Chunk(
                chunk_index=len(chunks),
                text="\n".join(p.text for p in buf),
                page=buf[0].page,
                para_index=buf[0].para_index,
                blocks=list(buf),
            )
        )

    for piece in pieces:
        # 加不下了：先结算当前块，下一块从上一块的末段开始（重叠）
        if buf and total + len(piece.text) > chunk_size:
            carry = buf[-1]
            flush()
            if overlap > 0 and len(carry.text) <= chunk_size // 2:
                buf = [carry]
                total = len(carry.text)
            else:
                buf = []
                total = 0
        buf.append(piece)
        total += len(piece.text) + 1
    if buf:
        flush()
    return chunks
