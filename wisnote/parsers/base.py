"""Block：文档解析的最小单位（一个段落），携带溯源用的位置元数据"""

from dataclasses import dataclass


@dataclass
class Block:
    page: int | None    # 页码（1 起）；docx/txt 无页码概念时为 None
    para_index: int      # 段落序号（文档内从 1 递增）
    text: str            # 段落文本
