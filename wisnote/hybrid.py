"""混合检索：BM25 关键词 + 语义向量，RRF 倒数排名融合

为什么用 RRF 而不是分数加权求和：
- BM25 分数无上界（取决于查询词数与语料 IDF），语义相似度落在 [0,1]，
  两者量纲不可比；归一化方法又依赖 query 与语料分布，小语料上不可靠。
- RRF 只用"排名"做融合，天然免疫量纲问题，且无需调参（k=60 为业界
  默认，Elasticsearch / LangChain 同款）。

对齐键说明：
- SQLite chunks（BM25 数据源）与 Chroma（向量数据源）的 chunk_id 格式
  不同（chunk{id} vs doc{doc}_chunk{idx}），不能用 chunk_id 直接合并；
- 两侧都记录 (document_id, chunk_index)，同一批切块入库时一一对应，
  用它做合并去重键。
"""

import threading

from wisnote.bm25 import BM25Index
from wisnote.config import RRF_K, SEARCH_TOP_K
from wisnote.db import get_conn
from wisnote.vectorstore import get_store


def rrf_fuse(
    sem_hits: list[dict],
    bm_hits: list[dict],
    top_k: int = SEARCH_TOP_K,
    rrf_k: int = RRF_K,
) -> list[dict]:
    """RRF 融合两条检索结果列表，返回按融合分排序的 Top-K

    融合分 = Σ 1/(rrf_k + rank)，同时在两侧命中的 chunk 得分更高。
    每个输出 hit 保留原始信息：
    - similarity：语义相似度（仅 BM25 命中的 chunk 为 0.0），
      防幻觉闸门（min_similarity 阈值）继续以此为依据，不受 BM25 影响；
    - bm25_score / sem_rank / bm25_rank：调试与可解释性用。
    """
    fused: dict[tuple, dict] = {}

    def _item(h: dict) -> dict:
        # 对齐键：入库时同一批切块两侧一一对应（见模块 docstring）
        key = (h.get("document_id"), h.get("chunk_index"))
        if key not in fused:
            fused[key] = {
                "chunk_id": h.get("chunk_id"),
                "text": h.get("text", ""),
                "document_id": h.get("document_id"),
                "chunk_index": h.get("chunk_index"),
                "page": h.get("page"),
                "para_index": h.get("para_index"),
                "similarity": 0.0,
                "bm25_score": None,
                "sem_rank": None,
                "bm25_rank": None,
                "rrf_score": 0.0,
            }
        return fused[key]

    for rank, h in enumerate(sem_hits, start=1):
        item = _item(h)
        item["rrf_score"] += 1.0 / (rrf_k + rank)
        item["similarity"] = h.get("similarity", 0.0)
        item["sem_rank"] = rank

    for rank, h in enumerate(bm_hits, start=1):
        item = _item(h)
        item["rrf_score"] += 1.0 / (rrf_k + rank)
        item["bm25_score"] = h.get("score")
        item["bm25_rank"] = rank

    ranked = sorted(
        fused.values(),
        key=lambda x: (-x["rrf_score"], x["sem_rank"] or 999, x["bm25_rank"] or 999),
    )
    return ranked[:top_k]


# ---------- BM25 索引缓存 ----------
# BM25Index 构造需要全量扫库 + jieba 分词（数百毫秒级），UI 每次提问
# 都重建会明显拖慢响应，因此按 user 缓存，用语料指纹检测失效。
# 指纹 = (COUNT(*), MAX(id))：id 是 AUTOINCREMENT 永不复用，
# 任何新增必使 MAX 增大、任何删除必使 COUNT 减小，故指纹可靠。
_BM25_CACHE: dict[int, tuple[tuple[int, int], BM25Index]] = {}
_CACHE_LOCK = threading.Lock()


def _corpus_fingerprint(user_id: int) -> tuple[int, int]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*), MAX(id) FROM chunks WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0], row[1] or 0


def get_bm25_index(user_id: int) -> BM25Index:
    """获取该用户的 BM25 索引（语料未变时复用缓存）"""
    fp = _corpus_fingerprint(user_id)
    with _CACHE_LOCK:
        cached = _BM25_CACHE.get(user_id)
        if cached is not None and cached[0] == fp:
            return cached[1]
    # 耗时的分词建索引放在锁外，避免阻塞并发请求；
    # 并发下可能重复构建（无害，后写覆盖），换响应速度
    idx = BM25Index(user_id)
    with _CACHE_LOCK:
        _BM25_CACHE[user_id] = (fp, idx)
    return idx


def reset_bm25_cache() -> None:
    """清空 BM25 索引缓存（测试用）"""
    with _CACHE_LOCK:
        _BM25_CACHE.clear()


def hybrid_search(user_id: int, query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """混合检索入口：语义 + BM25 各取 Top-K，RRF 融合后返回 Top-K"""
    sem_hits = get_store().search(user_id, query, top_k)
    bm_hits = get_bm25_index(user_id).search(query, top_k)
    return rrf_fuse(sem_hits, bm_hits, top_k)
