"""参数寻优实验：chunk_size × overlap 网格对比，找出最优参数组合

网格：CHUNK_SIZE ∈ {300, 500, 800} × CHUNK_OVERLAP ∈ {0, 50, 100}
对每种组合：重新切块 → 重新向量化（临时 ChromaDB 集合）→ 跑 gold set → 算指标。
查询向量只算一次（所有组合共用），实验结束自动清理临时集合，不污染正式数据。

运行方式（项目根目录，激活 venv 后）：
    python scripts/exp_params.py
"""

import csv
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wisnote.chunker import chunk_blocks
from wisnote.config import SEARCH_TOP_K
from wisnote.parsers import parse_file
from wisnote.vectorstore import get_store

EVAL_DIR = ROOT / "eval"
GOLDSET = EVAL_DIR / "goldset.csv"
CORPUS_LIST = EVAL_DIR / "corpus.txt"
OUT = EVAL_DIR / "results_params.csv"

SIZES = [300, 500, 800]
OVERLAPS = [0, 50, 100]


def load_cases() -> list:
    cases = []
    with open(GOLDSET, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            q = (row.get("question") or "").strip()
            if q and (row.get("type") or "normal").strip() != "negative":
                kws = [k.strip() for k in row["keywords"].split(";") if k.strip()]
                cat = (row.get("category") or "literal").strip() or "literal"
                cases.append((q, kws, cat))
    return cases


def load_corpus_files() -> list[Path]:
    files = []
    for line in CORPUS_LIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            files.append(ROOT / line)
    return [f for f in files if f.exists()]


def run_combo(store, files: list[Path], cases: list, q_embs: list, size: int, overlap: int) -> dict | None:
    col_name = f"eval_params_{size}_{overlap}"
    try:
        store.client.delete_collection(col_name)
    except Exception:
        pass
    col = store.client.get_or_create_collection(
        col_name, metadata={"hnsw:space": "cosine"}
    )

    ids, docs = [], []
    for f in files:
        blocks = parse_file(f)
        chunks = chunk_blocks(blocks, chunk_size=size, overlap=overlap)
        for i, c in enumerate(chunks):
            ids.append(f"{f.name}_{i}")
            docs.append(c.text)
    if not docs:
        store.client.delete_collection(col_name)
        return None
    col.add(ids=ids, documents=docs, embeddings=store.embed(docs))

    ranks: list[tuple[int | None, str]] = []
    for (q, kws, cat), qe in zip(cases, q_embs):
        res = col.query(query_embeddings=[qe], n_results=SEARCH_TOP_K)
        texts = res["documents"][0]
        r = next(
            (i for i, t in enumerate(texts, start=1) if any(k in t for k in kws)),
            None,
        )
        ranks.append((r, cat))

    store.client.delete_collection(col_name)

    def mrr(sub) -> float:
        return sum(1.0 / r for r, _ in sub if r is not None) / len(sub)

    lit = [x for x in ranks if x[1] == "literal"]
    par = [x for x in ranks if x[1] == "paraphrase"]
    n = len(ranks)
    return {
        "size": size,
        "overlap": overlap,
        "chunks": len(docs),
        "Recall@1": sum(1 for r, _ in ranks if r == 1) / n,
        "Recall@3": sum(1 for r, _ in ranks if r is not None and r <= 3) / n,
        "Recall@5": sum(1 for r, _ in ranks if r is not None and r <= 5) / n,
        "MRR": mrr(ranks),
        "MRR_literal": mrr(lit),
        "MRR_paraphrase": mrr(par),
        "missed": sum(1 for r, _ in ranks if r is None),
    }


def main() -> None:
    cases = load_cases()
    files = load_corpus_files()
    print(f"[实验] 语料 {len(files)} 个文件，标注 {len(cases)} 题")
    print(f"[实验] 网格：size={SIZES} × overlap={OVERLAPS}\n")

    store = get_store()
    q_embs = store.embed([q for q, _kws, _cat in cases])  # 查询向量只算一次

    results = []
    for size, overlap in itertools.product(SIZES, OVERLAPS):
        m = run_combo(store, files, cases, q_embs, size, overlap)
        if m:
            results.append(m)
            print(
                f"  size={m['size']:<4} overlap={m['overlap']:<4} "
                f"块数={m['chunks']:<3} R@1={m['Recall@1']:.1%} R@5={m['Recall@5']:.1%} "
                f"MRR={m['MRR']:.3f} (字面{m['MRR_literal']:.2f}/改写{m['MRR_paraphrase']:.2f}) "
                f"未命中={m['missed']}"
            )

    # 按 MRR 降序、Recall@5 降序排序，取最优
    results.sort(key=lambda m: (m["MRR"], m["Recall@5"], m["Recall@1"]), reverse=True)
    best = results[0]

    print("\n" + "=" * 80)
    print("排名（按 MRR 降序）：")
    print(f"{'rank':<6}{'size':>6}{'overlap':>10}{'R@1':>9}{'R@3':>9}{'R@5':>9}{'MRR':>9}{'未命中':>8}")
    for i, m in enumerate(results, 1):
        print(
            f"{i:<6}{m['size']:>6}{m['overlap']:>10}"
            f"{m['Recall@1']:>8.1%}{m['Recall@3']:>8.1%}{m['Recall@5']:>8.1%}"
            f"{m['MRR']:>9.3f}{m['missed']:>8}"
        )

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    print(f"\n最优组合：CHUNK_SIZE={best['size']}  CHUNK_OVERLAP={best['overlap']}  "
          f"(MRR={best['MRR']:.3f}, Recall@5={best['Recall@5']:.1%})")
    print(f"结果已导出：eval/results_params.csv")
    print("注意：并列最优时优先选块数适中（500上下）的组合，避免极端值过拟合小语料。")


if __name__ == "__main__":
    main()
