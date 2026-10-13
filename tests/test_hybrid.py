"""混合检索测试：RRF 融合纯函数 + BM25 缓存失效 + 跨库对齐端到端

对应设计要点：
- 融合只看排名、去重按 (document_id, chunk_index)（两侧 chunk_id 格式不同）；
- 仅 BM25 命中的 chunk similarity=0.0，不得抬升防幻觉闸门；
- 语料变化（count/max_id 指纹）必须触发 BM25 索引重建。
"""

import pytest

from wisnote.db import get_conn
from wisnote.hybrid import (
    get_bm25_index,
    hybrid_search,
    reset_bm25_cache,
    rrf_fuse,
)
from wisnote.ingest import ingest_document
from wisnote.rag import max_semantic_similarity


def _sem(doc_id: int, idx: int, sim: float) -> dict:
    """模拟语义检索 hit（对齐 vectorstore.search 返回结构）"""
    return {
        "chunk_id": f"doc{doc_id}_chunk{idx}",
        "text": f"语义文本{doc_id}-{idx}",
        "similarity": sim,
        "document_id": doc_id,
        "chunk_index": idx,
        "page": None,
        "para_index": None,
    }


def _bm(doc_id: int, idx: int, score: float) -> dict:
    """模拟 BM25 hit（对齐 BM25Index.search 返回结构）"""
    return {
        "text": f"BM文本{doc_id}-{idx}",
        "score": score,
        "document_id": doc_id,
        "chunk_index": idx,
        "page": None,
        "para_index": None,
        "chunk_id": f"chunk{doc_id * 100 + idx}",
    }


# ---------- RRF 融合纯函数 ----------

def test_rrf_dual_hit_outranks_singles():
    """同时命中两路 > 只命中一路（RRF 核心性质）"""
    fused = rrf_fuse(
        [_sem(1, 0, 0.8), _sem(2, 0, 0.7)],
        [_bm(1, 0, 5.0), _bm(3, 0, 4.0)],
    )
    assert fused[0]["document_id"] == 1, "双路命中的 chunk 应排第一"
    assert fused[0]["rrf_score"] == pytest.approx(1 / 61 + 1 / 61)
    assert fused[0]["similarity"] == 0.8, "语义相似度必须保留（闸门依据）"
    assert fused[0]["bm25_score"] == 5.0


def test_rrf_score_math_single_list():
    """只命中一路时的融合分 = 1/(60+rank)"""
    fused = rrf_fuse([_sem(1, 0, 0.5), _sem(1, 1, 0.4)], [])
    assert fused[0]["rrf_score"] == pytest.approx(1 / 61)
    assert fused[1]["rrf_score"] == pytest.approx(1 / 62)


def test_rrf_dedup_by_document_and_chunk_index():
    """同 (document_id, chunk_index) 去重合并，而不是按 chunk_id"""
    sem = [_sem(1, 2, 0.6)]
    bm = [_bm(1, 2, 9.9)]  # chunk_id 完全不同，但 (doc,index) 相同
    fused = rrf_fuse(sem, bm)
    assert len(fused) == 1, "同一 chunk 两路命中必须合并成一条"


def test_rrf_bm_only_hit_similarity_is_zero():
    """仅 BM25 命中：进入结果但 similarity=0.0，不抬升闸门"""
    fused = rrf_fuse([_sem(1, 0, 0.2)], [_bm(7, 3, 12.3)])
    bm_only = next(h for h in fused if h["document_id"] == 7)
    assert bm_only["similarity"] == 0.0
    assert bm_only["bm25_rank"] == 1
    assert bm_only["sem_rank"] is None
    assert max_semantic_similarity(fused) == 0.2, "BM25 独有命中不得抬升闸门"


def test_rrf_top_k_truncation():
    """结果截断到 top_k"""
    fused = rrf_fuse([_sem(i, 0, 0.9 - i * 0.1) for i in range(6)], [], top_k=3)
    assert len(fused) == 3


def test_rrf_empty_inputs():
    assert rrf_fuse([], []) == []


def test_max_semantic_similarity_edge_cases():
    assert max_semantic_similarity([]) == 0.0
    assert max_semantic_similarity([_sem(1, 0, 0.3), _sem(2, 0, 0.9)]) == 0.9


# ---------- BM25 缓存失效（依赖临时库夹具） ----------

def test_bm25_cache_rebuilds_on_corpus_change(user_id, tmp_path):
    """语料变化（count/max_id 指纹变化）后缓存必须重建"""
    reset_bm25_cache()
    p1 = tmp_path / "cache_a.txt"
    p1.write_text("缓存失效验证：甲文档包含独有词汇驼鸟算法。", encoding="utf-8")
    ingest_document(user_id, str(p1), title="cacheA")

    idx1 = get_bm25_index(user_id)
    idx1_again = get_bm25_index(user_id)
    assert idx1 is idx1_again, "语料未变时应复用缓存（同一对象）"
    # "斑马协议" 只存在于尚未入库的乙文档，甲文档无任何重合词
    assert idx1.search("斑马协议", 1) == [], "乙文档未入库，检索不应命中"

    p2 = tmp_path / "cache_b.txt"
    p2.write_text("乙文档：斑马协议是新词。", encoding="utf-8")
    ingest_document(user_id, str(p2), title="cacheB")

    idx2 = get_bm25_index(user_id)
    assert idx2 is not idx1, "语料已变，必须重建索引"
    hits = idx2.search("斑马协议", 3)
    assert any("斑马协议" in h["text"] for h in hits), "重建后的索引能检索新入库内容"


# ---------- 端到端：SQLite 与 Chroma 两路对齐 ----------

def test_hybrid_search_cross_store_alignment(user_id, tmp_path):
    """ingest 后混合检索：融合结果的关键信息能对回 SQLite chunks 行

    这是混合检索最大的坑位验证：SQLite（BM25）与 Chroma（语义）的
    chunk_id 格式不同，唯一可靠对齐键是 (document_id, chunk_index)。
    """
    reset_bm25_cache()
    marker = "十字星座行星HD40307专用锚点"
    p = tmp_path / "align.txt"
    p.write_text(
        f"天文笔记：{marker} 是一颗位于南天的恒星，相关讨论见后文。"
        "补充一段与问题无关的长文本以便切块产生上下文。" * 5,
        encoding="utf-8",
    )
    ingest_document(user_id, str(p), title="对齐测试文档")

    hits = hybrid_search(user_id, marker, 5)
    assert hits, "混合检索应至少命中 BM25 路（字面精确词）"

    conn = get_conn()
    try:
        rows = {
            (r["document_id"], r["chunk_index"]): r["text"]
            for r in conn.execute(
                "SELECT document_id, chunk_index, text FROM chunks WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }
    finally:
        conn.close()

    for h in hits:
        key = (h["document_id"], h["chunk_index"])
        assert key in rows, f"融合结果的 ({key}) 必须能对回 SQLite chunks 行"
        assert h["text"] == rows[key], "两库同键文本必须一致"
    assert any(marker in h["text"] for h in hits), "锚点词所在 chunk 应被检索到"
