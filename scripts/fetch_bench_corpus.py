"""下载并抽样公开中文检索基准（C-MTEB/T2Retrieval）→ 生成 WisNote 评测语料

T2Retrieval：真实搜索查询 + 中文网页段落 + 人工标注相关性（qrels），
C-MTEB 中文检索评测的事实标准之一。

流程：
  1. 从 hf-mirror 下载 corpus / queries / qrels 三个 parquet；
  2. 固定随机种子抽 600 条有标注的查询；
  3. 语料 = 这些查询的全部标注段落（答案必须在内）+ 随机填充段落，共 2 万段；
  4. 网页文本轻清洗（去 HTML 标签）；每 20 段拼成一个 .txt 笔记文件，
     段落加"【条目 {pid}】"标记行（入库切块后按标记映射回原段落）；
  5. 导出 eval/bench_queries.csv（查询+标注）供评测脚本使用。

运行：python scripts/fetch_bench_corpus.py
"""

import csv
import os
import random
import re
import sys
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

OUT_NOTES = ROOT / "data" / "bench" / "notes"
OUT_QUERIES = ROOT / "eval" / "bench_queries.csv"

CORPUS_REPO = "C-MTEB/T2Retrieval"
QRELS_REPO = "C-MTEB/T2Retrieval-qrels"

N_QUERIES = 600       # 抽样查询数
N_PASSAGES = 20000    # 语料段落总数（含标注段 + 随机填充）
PER_FILE = 20         # 每个笔记文件的段落数
MIN_PASSAGE_CHARS = 30  # 清洗后短于此的段落直接丢弃（当作噪声）
SEED = 13

# qrels 里 score>=1 视为相关（T2 标注分 0/1/2/3 档，>=1 都算命中）
MIN_SCORE = 1

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000]+")


def clean_text(t: str) -> str:
    """网页文本轻清洗：去 HTML 标签、合并空白（保留换行）"""
    t = (t or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    t = _TAG_RE.sub("", t)
    t = _WS_RE.sub(" ", t)
    t = "\n".join(line.strip() for line in t.splitlines())
    return t.strip()


def _load(repo: str, filename: str) -> list[dict]:
    path = hf_hub_download(repo_id=repo, filename=filename, repo_type="dataset")
    return pq.read_table(path).to_pylist()


def main() -> None:
    print("[1/5] 下载 parquet（首次约 160MB，走 hf-mirror；已下载则秒过）...")
    corpus = _load(CORPUS_REPO, "data/corpus-00000-of-00001-8afe7b7a7eca49e3.parquet")
    queries = _load(CORPUS_REPO, "data/queries-00000-of-00001-930bf3b805a80dd9.parquet")
    qrels = _load(QRELS_REPO, "data/dev-00000-of-00001-92ed0416056ff7e1.parquet")
    print(f"  语料 {len(corpus)} 段 | 查询 {len(queries)} 条 | 标注 {len(qrels)} 行")

    # qrels: qid / pid / score（列名见 T2Retrieval-qrels 实际 schema）
    qid_to_pids: dict[str, set[str]] = {}
    for r in qrels:
        if int(r["score"]) >= MIN_SCORE:
            qid_to_pids.setdefault(str(r["qid"]), set()).add(str(r["pid"]))

    # 语料清洗 + 建索引（短段落丢弃）
    corpus_by_pid: dict[str, str] = {}
    dropped = 0
    for r in corpus:
        body = clean_text(r["text"])
        if len(body) < MIN_PASSAGE_CHARS:
            dropped += 1
            continue
        corpus_by_pid[str(r["id"])] = body
    # 标注段落若被清洗丢弃，从查询的标注里剔除；标空了的查询直接作废
    query_text = {str(r["id"]): clean_text(r["text"]) for r in queries}
    print(f"  清洗后语料 {len(corpus_by_pid)} 段（丢弃过短 {dropped}）")

    # [2/5] 抽查询
    rng = random.Random(SEED)
    valid_qids = [
        q for q, pids in qid_to_pids.items()
        if query_text.get(q) and pids and all(p in corpus_by_pid for p in pids)
    ]
    print(f"[2/5] 有效标注查询 {len(valid_qids)} 条，抽样 {N_QUERIES} 条")
    sampled_qids = rng.sample(valid_qids, min(N_QUERIES, len(valid_qids)))

    # [3/5] 组语料：标注段必含 + 随机填充
    must_pids = set().union(*(qid_to_pids[q] for q in sampled_qids))
    pool_pids = [pid for pid in corpus_by_pid if pid not in must_pids]
    n_filler = max(0, N_PASSAGES - len(must_pids))
    filler = rng.sample(pool_pids, min(n_filler, len(pool_pids)))
    all_pids = sorted(must_pids) + filler
    print(f"[3/5] 语料 = {len(must_pids)} 个标注段 + {len(filler)} 个填充段 = {len(all_pids)} 段")

    # [4/5] 写笔记文件（每 20 段一个文件，段落带标记行）
    OUT_NOTES.mkdir(parents=True, exist_ok=True)
    for f in OUT_NOTES.glob("*.txt"):
        f.unlink()
    n_files = 0
    for i in range(0, len(all_pids), PER_FILE):
        parts = [f"【条目 {pid}】\n{corpus_by_pid[pid]}" for pid in all_pids[i : i + PER_FILE]]
        n_files += 1
        (OUT_NOTES / f"bench_{n_files:04d}.txt").write_text("\n\n".join(parts), encoding="utf-8")
    total_chars = sum(len(corpus_by_pid[p]) for p in all_pids)
    print(f"[4/5] 已生成 {n_files} 个笔记文件（{total_chars / 10000:.0f} 万字符）")
    print(f"      预计切块数 ≈ {total_chars // 500 + n_files} 块（500字/块）")

    # [5/5] 导出查询集
    with open(OUT_QUERIES, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "query", "qrel_pids"])
        for q in sampled_qids:
            w.writerow([q, query_text[q], ";".join(sorted(qid_to_pids[q]))])
    print(f"[5/5] 查询集已导出 eval/bench_queries.csv（{len(sampled_qids)} 条）")
    print("\n下一步：python scripts/eval_bench.py ingest  → 入库并建 chunk→段落映射")
    print("      python scripts/eval_bench.py eval      → 语义/BM25/混合 三路对比")


if __name__ == "__main__":
    main()
