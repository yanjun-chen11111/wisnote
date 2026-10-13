"""大规模检索评测：公开基准 T2Retrieval（语义 vs BM25 vs 混合RRF 三路对比）

语料：C-MTEB/T2Retrieval 抽样（600 条真实搜索查询 + 2 万段落，
其中 2999 个是查询的标注答案段，其余 17001 个为干扰段），
由 scripts/fetch_bench_corpus.py 生成（约 1000 个笔记文件、数万块）。

命中判定（chunk→段落 两层映射，三路方法同一把尺子）：
  1) 块文本含标注段落的标记行"【条目 {pid}]"；
  2) 块的文本片段（≥40字符的行）包含在标注段落原文里
     （长段落被切成多块、后续块无标记时的兜底）。

用法：
  python scripts/eval_bench.py ingest   # 入库（约10-20分钟，含嵌入）
  python scripts/eval_bench.py eval     # 跑三路对比
"""

import csv
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wisnote import auth
from wisnote.bm25 import BM25Index
from wisnote.db import get_conn
from wisnote.hybrid import rrf_fuse
from wisnote.ingest import delete_document, ingest_document, list_documents
from wisnote.vectorstore import get_store

NOTES_DIR = ROOT / "data" / "bench" / "notes"
QUERIES_CSV = ROOT / "eval" / "bench_queries.csv"
DETAIL_OUT = ROOT / "eval" / "bench_results_detail.csv"
BENCH_USER, BENCH_PASS = "bench_user", "bench123"
TOP_K = 10          # 评测取 Top-10（段落→块粒度映射后同名段落可占多块）
MARKER_RE = re.compile(r"【条目 (\d+)】")
MIN_PIECE = 40      # 块内片段参与"包含判定"的最小字符数


def _bench_uid() -> int:
    try:
        auth.register(BENCH_USER, BENCH_PASS)
    except auth.AuthError:
        pass
    return auth.login(BENCH_USER, BENCH_PASS)["user_id"]


def load_passages() -> dict[str, str]:
    """从生成的笔记文件重建 pid → 段落原文 映射（评测命中判定要用）"""
    pid_text: dict[str, str] = {}
    for f in sorted(NOTES_DIR.glob("*.txt")):
        content = f.read_text(encoding="utf-8")
        parts = re.split(r"【条目 (\d+)】\n", content)
        # re.split 交替产出 [前导, pid, 正文, pid, 正文...]
        for i in range(1, len(parts), 2):
            pid_text[parts[i]] = parts[i + 1].strip()
    return pid_text


def chunk_hits_qrel(chunk_text: str, qrel_pids: set[str], passages: dict[str, str]) -> bool:
    """块是否命中任一标注段落（标记 或 文本片段包含）"""
    if MARKER_RE.search(chunk_text):
        pids = {m for m in MARKER_RE.findall(chunk_text)}
        if pids & qrel_pids:
            return True
    pieces = [p for p in chunk_text.split("\n") if len(p) >= MIN_PIECE]
    return any(p in passages[qp] for qp in qrel_pids for p in pieces)


def rank_of_qrel(hits: list[dict], qrel_pids: set[str], passages: dict[str, str]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if chunk_hits_qrel(h["text"], qrel_pids, passages):
            return i
    return None


def metrics(ranks: list) -> dict:
    n = len(ranks)
    return {
        "Recall@1": sum(1 for r in ranks if r == 1) / n,
        "Recall@3": sum(1 for r in ranks if r is not None and r <= 3) / n,
        "Recall@5": sum(1 for r in ranks if r is not None and r <= 5) / n,
        "Recall@10": sum(1 for r in ranks if r is not None and r <= 10) / n,
        "MRR": sum(1.0 / r for r in ranks if r is not None) / n,
    }


def do_ingest() -> None:
    uid = _bench_uid()
    # 断点续跑：只清半成品（非 ready），已完成的跳过不重做
    done: set[str] = set()
    for d in list_documents(uid):
        if d["status"] == "ready":
            done.add(d["filename"])
        else:
            print(f"[清理] 删除半成品文档 {d['filename']}（status={d['status']}）")
            delete_document(uid, d["id"])
    files = sorted(NOTES_DIR.glob("*.txt"))
    if not files:
        sys.exit("没有语料文件，请先运行 scripts/fetch_bench_corpus.py")
    todo = [f for f in files if f.name not in done]
    print(f"[入库] 共 {len(files)} 文件，已完成 {len(done)}，本次需入库 {len(todo)} 个")
    if not todo:
        print("[完成] 全部已入库。下一步：python scripts/eval_bench.py eval")
        return
    t0 = time.time()
    done_now = 0
    for f in todo:
        ingest_document(uid, str(f), course_name="公开基准语料")
        done_now += 1
        if done_now % 50 == 0 or done_now == len(todo):
            dt = time.time() - t0
            print(
                f"  {done_now}/{len(todo)}（{dt:.0f}s，"
                f"预计剩余 {dt / done_now * (len(todo) - done_now):.0f}s）",
                flush=True,
            )
    conn = get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE user_id = ?", (uid,)
        ).fetchone()[0]
    finally:
        conn.close()
    print(f"[完成] 入库 {len(todo)} 文件 → bench 账号共 {n} 块。"
          "下一步：python scripts/eval_bench.py eval")


def do_eval() -> None:
    if not QUERIES_CSV.exists():
        sys.exit("找不到 eval/bench_queries.csv，请先运行 fetch + ingest")
    uid = _bench_uid()
    conn = get_conn()
    try:
        n_chunks = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE user_id = ?", (uid,)
        ).fetchone()[0]
    finally:
        conn.close()
    if n_chunks == 0:
        sys.exit("bench 账号语料为空，请先运行 eval_bench.py ingest")
    print(f"[语料] bench 账号共 {n_chunks} 块")

    with open(QUERIES_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    queries = [(r["qid"], r["query"], set(r["qrel_pids"].split(";"))) for r in rows]
    print(f"[查询] {len(queries)} 条真实搜索查询，每路取 Top-{TOP_K}")

    passages = load_passages()
    print(f"[映射] 重建段落映射 {len(passages)} 段")

    print("[索引] 构建 BM25 索引（数万块分词，一次性，需数分钟）...")
    t0 = time.time()
    bm25 = BM25Index(uid)
    print(f"  BM25 索引就绪（{time.time() - t0:.0f}s，{bm25.N} 块）")
    store = get_store()

    sem_r, bm_r, hyb_r = [], [], []
    t0 = time.time()
    for i, (qid, q, qrels) in enumerate(queries, 1):
        sem_hits = store.search(uid, q, TOP_K)
        bm_hits = bm25.search(q, TOP_K)
        hyb_hits = rrf_fuse(sem_hits, bm_hits, TOP_K)
        sem_r.append(rank_of_qrel(sem_hits, qrels, passages))
        bm_r.append(rank_of_qrel(bm_hits, qrels, passages))
        hyb_r.append(rank_of_qrel(hyb_hits, qrels, passages))
        if i % 100 == 0 or i == len(queries):
            print(f"  {i}/{len(queries)}（累计 {time.time() - t0:.0f}s）")

    table = [
        ("语义(bge)", metrics(sem_r)),
        ("BM25", metrics(bm_r)),
        ("混合RRF", metrics(hyb_r)),
    ]
    print("\n【T2Retrieval 抽样评测：600 条真实搜索查询 × 3 路】")
    header = f"{'指标':<11}" + "".join(f"{name:>12}" for name, _ in table)
    print(header)
    print("-" * len(header))
    for k in ("Recall@1", "Recall@3", "Recall@5", "Recall@10"):
        print(f"{k:<11}" + "".join(f"{m[k]:>11.1%}" for _, m in table))
    print(f"{'MRR':<11}" + "".join(f"{m['MRR']:>11.3f}" for _, m in table))

    with open(DETAIL_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "query", "semantic_rank", "bm25_rank", "hybrid_rank"])
        for (qid, q, _), r1, r2, r3 in zip(queries, sem_r, bm_r, hyb_r):
            w.writerow([qid, q, r1 or "", r2 or "", r3 or ""])
    print("\n逐题明细已导出 eval/bench_results_detail.csv")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("ingest", "eval"):
        print(__doc__)
        sys.exit("用法: python scripts/eval_bench.py [ingest|eval]")
    {"ingest": do_ingest, "eval": do_eval}[sys.argv[1]]()
