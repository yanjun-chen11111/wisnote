"""BM25 关键词检索（Okapi BM25，k1=1.5, b=0.75，中文分词用 jieba）

用途：与语义向量检索做 RRF 融合（见 wisnote/hybrid.py），
弥补向量检索在专有名词 / ID / 缩写等"字面精确匹配"场景的漏检。
也可单独作为评测基线（scripts/eval_retrieval.py）。
"""

import math
from collections import Counter

import jieba

from wisnote.db import get_conn


class BM25Index:
    def __init__(self, user_id: int):
        """基于用户已入库的全部 chunks 构建 BM25 索引"""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, document_id, chunk_index, page, para_index, text FROM chunks "
                "WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        finally:
            conn.close()
        self.docs = [dict(r) for r in rows]
        self.tokens = [self._tokenize(d["text"]) for d in self.docs]
        # 词频预计算：search() 高频调用（UI 每次提问都要跑），临时重建
        # 全库 Counter 是 O(全库×段长)，大语料下每查询数百毫秒，预计算后建一次换查询快几个量级
        self.tfs = [Counter(t) for t in self.tokens]
        self.N = len(self.docs)
        self.avgdl = (sum(len(t) for t in self.tokens) / self.N) if self.N else 0.0
        self.df: Counter = Counter()
        for toks in self.tokens:
            self.df.update(set(toks))

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        # 中文分词；英文/数字统一小写
        return [w.lower() for w in jieba.lcut(text) if not w.isspace()]

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.N - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """返回 Top-K，结构对齐语义检索的结果（score 代替 similarity）"""
        if self.N == 0:
            return []
        k1, b = 1.5, 0.75
        q_tokens = self._tokenize(query)
        scores: list[float] = []
        for i, toks in enumerate(self.tokens):
            tf = self.tfs[i]
            dl = len(toks)
            s = 0.0
            for t in q_tokens:
                f = tf.get(t, 0)
                if f == 0:
                    continue
                s += self._idf(t) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avgdl))
            scores.append(s)
        ranked = sorted(range(self.N), key=lambda i: scores[i], reverse=True)
        hits = []
        for i in ranked[:top_k]:
            if scores[i] <= 0:
                break
            d = self.docs[i]
            hits.append(
                {
                    "text": d["text"],
                    "score": scores[i],
                    "document_id": d["document_id"],
                    "chunk_index": d["chunk_index"],
                    "page": d["page"],
                    "para_index": d["para_index"],
                    "chunk_id": f"chunk{d['id']}",
                }
            )
        return hits
