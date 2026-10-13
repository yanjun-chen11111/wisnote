"""检索质量评测（完整版）：语义检索 vs BM25 基线 vs 混合RRF + gold set 命中率 + 负例对照

流程：
  1. 以专用评测账号登录，按 eval/corpus.txt 重建评测语料（用当前 config 的切块参数）；
  2. 逐条读取 eval/goldset.csv 的标注（literal 字面题 / paraphrase 改写题 / negative 负例）；
  3. 三种方法各取 Top-5，记录命中排名（语义=hybrid=False 基线；混合=产品默认管线）；
  4. 输出 总体 + 分类 的 Recall@1/3/5、MRR 对比表；
  5. 逐题明细导出 eval/results_detail.csv；
  6. 负例：无关问题在混合检索下最高语义相似度应 < 0.4（防幻觉闸门有效）。

指标定义（论文可引用）：
  Recall@K = 命中排名 ≤ K 的问题数 / 总题数
  MRR = Σ(1/命中排名) / 总题数

运行方式（项目根目录，激活 venv 后）：
    python scripts/eval_retrieval.py
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wisnote import auth
from wisnote.bm25 import BM25Index
from wisnote.config import CHUNK_OVERLAP, CHUNK_SIZE
from wisnote.ingest import delete_document, ingest_document, list_documents
from wisnote.rag import max_semantic_similarity
from wisnote.search import search_notes

EVAL_DIR = ROOT / "eval"
GOLDSET = EVAL_DIR / "goldset.csv"
CORPUS_LIST = EVAL_DIR / "corpus.txt"
DETAIL_OUT = EVAL_DIR / "results_detail.csv"
IRRELEVANT_THRESHOLD = 0.4
TOP_K = 5
EVAL_USER, EVAL_PASS = "eval_user", "eval123"


def load_goldset() -> tuple[list, list]:
    cases, negatives = [], []
    with open(GOLDSET, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            q = (row.get("question") or "").strip()
            if not q:
                continue
            if (row.get("type") or "normal").strip() == "negative":
                negatives.append(q)
            else:
                kws = [k.strip() for k in row["keywords"].split(";") if k.strip()]
                cat = (row.get("category") or "literal").strip() or "literal"
                cases.append((q, kws, cat))
    return cases, negatives


def setup_corpus(uid: int) -> None:
    """清空评测账号旧数据，按清单重建语料"""
    for d in list_documents(uid):
        delete_document(uid, d["id"])
    files = [
        line.strip()
        for line in CORPUS_LIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    n = 0
    for rel in files:
        path = ROOT / rel
        if not path.exists():
            print(f"[警告] 语料文件不存在，已跳过：{rel}")
            continue
        ingest_document(uid, path, course_name="评测语料")
        n += 1
    print(f"[语料] 已入库 {n} 个文件（清单共 {len(files)} 个）")


def rank_of(results: list[dict], keywords: list[str]) -> int | None:
    """返回首个包含任一关键词的排名；未命中返回 None"""
    for i, h in enumerate(results, start=1):
        if any(k in h["text"] for k in keywords):
            return i
    return None


def run_retrieval(search_fn, cases: list) -> list:
    """逐题检索，返回与 cases 对齐的排名列表"""
    return [rank_of(search_fn(q), kws) for q, kws, _cat in cases]


def metrics_from_ranks(ranks: list) -> dict:
    n = len(ranks)
    return {
        "Recall@1": sum(1 for r in ranks if r == 1) / n,
        "Recall@3": sum(1 for r in ranks if r is not None and r <= 3) / n,
        "Recall@5": sum(1 for r in ranks if r is not None and r <= 5) / n,
        "MRR": sum((1.0 / r) for r in ranks if r is not None) / n,
    }


def print_metric_table(title: str, methods: list[tuple[str, dict]]) -> None:
    """指标对比表：methods = [(方法名, 指标dict), ...]"""
    print(title)
    header = f"{'指标':<10}" + "".join(f"{name:>18}" for name, _ in methods)
    print(header)
    print("-" * (10 + 18 * len(methods)))
    for key in ("Recall@1", "Recall@3", "Recall@5"):
        row = f"{key:<10}" + "".join(f"{m[key]:>17.1%}" for _, m in methods)
        print(row)
    row = f"{'MRR':<10}" + "".join(f"{m['MRR']:>17.3f}" for _, m in methods)
    print(row)
    print()


def main() -> None:
    try:
        auth.register(EVAL_USER, EVAL_PASS)
    except auth.AuthError:
        pass
    user = auth.login(EVAL_USER, EVAL_PASS)
    uid = user["user_id"]

    print(f"[参数] 当前切块配置：CHUNK_SIZE={CHUNK_SIZE} CHUNK_OVERLAP={CHUNK_OVERLAP}")
    setup_corpus(uid)
    cases, negatives = load_goldset()
    cats = [c[2] for c in cases]
    print(f"[标注集] 正例 {len(cases)} 题（字面 {cats.count('literal')} / 改写 {cats.count('paraphrase')}），"
          f"负例 {len(negatives)} 题\n")

    bm25 = BM25Index(uid)
    sem_ranks = run_retrieval(lambda q: search_notes(uid, q, TOP_K, hybrid=False), cases)
    bm_ranks = run_retrieval(lambda q: bm25.search(q, TOP_K), cases)
    hyb_ranks = run_retrieval(lambda q: search_notes(uid, q, TOP_K, hybrid=True), cases)

    print_metric_table(
        "【总体】",
        [
            ("语义检索(bge)", metrics_from_ranks(sem_ranks)),
            ("BM25关键词", metrics_from_ranks(bm_ranks)),
            ("混合RRF", metrics_from_ranks(hyb_ranks)),
        ],
    )
    for cat, title in (
        ("literal", "【字面题】问题中含原文术语"),
        ("paraphrase", "【改写题】口语化同义表达"),
    ):
        idx = [i for i, c in enumerate(cases) if c[2] == cat]
        if not idx:
            continue
        print_metric_table(
            f"{title}（{len(idx)}题）",
            [
                ("语义检索(bge)", metrics_from_ranks([sem_ranks[i] for i in idx])),
                ("BM25关键词", metrics_from_ranks([bm_ranks[i] for i in idx])),
                ("混合RRF", metrics_from_ranks([hyb_ranks[i] for i in idx])),
            ],
        )

    print("逐题明细（√=混合检索命中；- = 未命中）：")
    with open(DETAIL_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "category", "semantic_rank", "bm25_rank", "hybrid_rank"])
        for (q, _kws, cat), r1, r2, r3 in zip(cases, sem_ranks, bm_ranks, hyb_ranks):
            flag = "√" if r3 else "×"
            print(f"  [{flag}] ({cat:<9}) {q}")
            print(f"        语义:Top{r1 or '-'}  |  BM25:Top{r2 or '-'}  |  混合:Top{r3 or '-'}")
            w.writerow([q, cat, r1 or "", r2 or "", r3 or ""])
    print("\n逐题明细已导出：eval/results_detail.csv")

    print(f"\n负例测试（混合检索下最高语义相似度应 < {IRRELEVANT_THRESHOLD}，闸门可拒答）：")
    for q in negatives:
        results = search_notes(uid, q, top_k=3)  # 默认混合管线，即产品实际路径
        mx = max_semantic_similarity(results)
        ok = "通过" if mx < IRRELEVANT_THRESHOLD else "偏高，需关注"
        print(f"  {q}  最高语义相似度={mx:.3f}  [{ok}]")

    auth.logout(user["token"])
    print("\n评测完成。以上对比表 + 明细 CSV 可直接用于论文实验章节。")


if __name__ == "__main__":
    main()
