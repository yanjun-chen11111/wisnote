"""全局配置：路径、切块参数、模型名称"""

from pathlib import Path

# 项目根目录（wisnote/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据目录（已加入 .gitignore）
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"      # 用户上传的原始文件
DB_PATH = DATA_DIR / "wisnote.db"       # SQLite 元数据库
CHROMA_DIR = DATA_DIR / "chroma"        # ChromaDB 向量库

# 文本切块参数（字符数，中文经验值：500 字一块 + 50 字重叠）
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Embedding 模型（约100MB，CPU毫秒级）
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

# 大语言模型：本地 Ollama 的 Qwen3-1.7B（单模型多任务，常驻内存）
OLLAMA_HOST = "http://127.0.0.1:11434"
LLM_MODEL = "qwen3:1.7b"
LLM_TIMEOUT = 300          # 纯CPU生成较慢，超时给足

# 纯CPU推理参数（对应计划书 3.4：16线程 + 限制生成长度）
LLM_OPTIONS = {
    "temperature": 0.3,     # 问答场景要稳，不要发散
    "top_p": 0.9,
    "num_predict": 512,     # 限制输出长度，CPU 上省时间
    "num_thread": 16,       # 用满 16 核（请求级参数，优先级高于环境变量）
    # 上下文长度：Ollama 默认仅 4096，塞 5 个笔记片段 + 多轮历史会被截断，
    # 这里调大到 8192（32GB 内存足够；若想更快可改回 4096）
    "num_ctx": 8192,
}

# 语义检索默认返回条数
SEARCH_TOP_K = 5

# 混合检索（BM25 关键词 + 语义向量，RRF 倒数排名融合）
HYBRID_RETRIEVAL = True   # search_notes 是否融合 BM25（False 则回退纯语义）
RRF_K = 60                # RRF 平滑常数，业界默认值（Elasticsearch/LangChain 同款）

# 登录令牌有效期（小时）：过期必须重新登录。
# 令牌存在网址参数里可能被他人看到，设置较短的有效期可限制风险。
SESSION_TTL_HOURS = 12

# 多轮对话上下文管理
HISTORY_TURNS = 8           # 带入模型的消息条数（8 条 ≈ 4 轮问答）
HISTORY_MAX_CHARS = 500     # 每条历史消息最大字数（防止上下文爆炸）
AUTO_COMPRESS_THRESHOLD = 24  # 消息超过该条数时，自动把更早的对话压缩为摘要

# 支持的文件类型（文本类走解析/切块/向量化；图片类仅存档预览，不参与 AI 检索）
SUPPORTED_EXTS = {
    ".pdf", ".pptx", ".docx", ".txt", ".md",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
}

# 图片类扩展名：仅存档 + 预览，不解析文本、不向量化
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


def ensure_dirs() -> None:
    """确保数据目录存在（首次运行时自动创建）"""
    for d in (DATA_DIR, UPLOADS_DIR, CHROMA_DIR):
        d.mkdir(parents=True, exist_ok=True)
