"""向量库：bge-small-zh-v1.5 嵌入 + ChromaDB 持久化

按用户隔离集合（collection），每个用户一个 notes_user_{id}，
后续社区阶段另建 community 集合。
"""

import os
import threading
from pathlib import Path

# HuggingFace 国内镜像（必须在 sentence-transformers 导入前设置，
# 否则首次下载 bge-small 模型会因 huggingface.co 被墙而失败）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import chromadb

from wisnote.config import CHROMA_DIR, EMBEDDING_MODEL, ensure_dirs
from wisnote.chunker import Chunk


class VectorStore:
    def __init__(self) -> None:
        ensure_dirs()
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self._model = None
        self._lock = threading.Lock()

    # ---------- 模型 ----------
    @property
    def model(self):
        """懒加载 Embedding 模型（首次调用时下载，约100MB）"""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        embs = self.model.encode(texts, normalize_embeddings=True)
        return embs.tolist()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """公开接口：文本 → 向量（评测/实验脚本使用）"""
        return self._embed(texts)

    # ---------- 集合 ----------
    def collection(self, user_id: int):
        return self.client.get_or_create_collection(
            name=f"notes_user_{user_id}",
            metadata={"hnsw:space": "cosine"},
        )

    # ---------- 写入 / 删除 ----------
    def add_chunks(self, user_id: int, document_id: int, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        col = self.collection(user_id)
        embeddings = self._embed([c.text for c in chunks])
        ids, metas, docs = [], [], []
        for i, c in enumerate(chunks):
            ids.append(f"doc{document_id}_chunk{i}")
            meta: dict = {
                "document_id": document_id,
                "chunk_index": c.chunk_index,
            }
            if c.page is not None:
                meta["page"] = c.page
            if c.para_index is not None:
                meta["para_index"] = c.para_index
            metas.append(meta)
            docs.append(c.text)
        col.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)

    def delete_document(self, user_id: int, document_id: int) -> None:
        self.collection(user_id).delete(where={"document_id": document_id})

    # ---------- 检索 ----------
    def search(self, user_id: int, query: str, top_k: int = 5) -> list[dict]:
        col = self.collection(user_id)
        count = col.count()
        if count == 0:
            return []
        res = col.query(
            query_embeddings=[self._embed([query])[0]],
            n_results=min(top_k, count),
        )
        hits: list[dict] = []
        ids = res["ids"][0]
        for i, cid in enumerate(ids):
            meta = res["metadatas"][0][i] or {}
            hits.append(
                {
                    "chunk_id": cid,
                    "text": res["documents"][0][i],
                    "similarity": 1.0 - (res["distances"][0][i]),  # cosine 距离 → 相似度
                    "document_id": meta.get("document_id"),
                    "page": meta.get("page"),
                    "para_index": meta.get("para_index"),
                }
            )
        return hits


# ---------- 模块级单例（模型只加载一次，常驻内存） ----------
_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_store() -> VectorStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = VectorStore()
    return _store
