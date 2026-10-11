"""从中文维基教科书（zh.wikibooks.org，CC BY-SA 3.0）抓取领域语料，用于检索评测

产出：
  samples/wikibooks/<学科>/<页面>.txt   每个页面一个文件（模拟"一份资料"）
  eval/corpus.txt                        评测语料清单
  eval/SOURCES.md                        来源与许可声明（论文附录用）

运行方式（项目根目录，激活 venv 后）：
    python scripts/fetch_corpus.py
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://zh.wikibooks.org/w/api.php"
UA = {"User-Agent": "WisNote-CorpusFetcher/1.0 (graduation project)"}

# 计算机主课 + 理工基础课
SUBJECTS = [
    "数据结构", "操作系统", "计算机网络", "数据库", "计算机组成原理", "编译原理",
    "算法", "离散数学", "程序设计", "高等数学", "线性代数", "概率论",
    "大学物理", "微积分", "统计学", "电路",
]

MAX_PAGES_PER_SUBJECT = 25   # 每学科最多抓多少页
MIN_CHARS = 800              # 太短的页面丢弃
MAX_CHARS_PER_PAGE = 20000   # 单页截断


def api(params: dict, retries: int = 3) -> dict:
    """带重试与退避的 API 请求（国内访问维基媒体常超时）"""
    url = API + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:      # noqa: BLE001 - 网络错误统一重试
            last = e
            time.sleep(1.5 * (i + 1))   # 退避：1.5s / 3s / 4.5s
    raise last


def list_pages(prefix: str, limit: int = 50) -> list[str]:
    """按标题前缀列出页面"""
    data = api({
        "action": "query", "format": "json", "list": "allpages",
        "apprefix": prefix, "aplimit": str(limit), "apnamespace": "0",
    })
    return [p["title"] for p in data.get("query", {}).get("allpages", [])]


def search_pages(kw: str, limit: int = 30) -> list[str]:
    """前缀没结果时，改用搜索"""
    data = api({
        "action": "query", "format": "json", "list": "search",
        "srsearch": kw, "srlimit": str(limit), "srnamespace": "0",
    })
    return [p["title"] for p in data.get("query", {}).get("search", [])]


def get_extract(title: str) -> str:
    """取页面纯文本"""
    data = api({
        "action": "query", "format": "json", "prop": "extracts",
        "explaintext": "1", "redirects": "1", "titles": title,
    })
    pages = data.get("query", {}).get("pages", {})
    for _pid, p in pages.items():
        t = p.get("extract")
        if t:
            return t
    return ""


def clean(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [l.rstrip() for l in text.splitlines()]
    return "\n".join(lines).strip()


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "_", s).strip().strip(".")
    return (s[:60] or "page")


def main() -> None:
    out_dir = ROOT / "samples" / "wikibooks"
    out_dir.mkdir(parents=True, exist_ok=True)
    seen_content: set[str] = set()
    sources: list[tuple[str, str, int]] = []   # (文件路径, 页面URL, 字数)
    summary: list[tuple[str, int, int]] = []   # (学科, 文件数, 总字数)

    for subject in SUBJECTS:
        titles = list_pages(subject)
        if len(titles) < 3:
            titles = search_pages(subject)
        titles = list(dict.fromkeys(titles))[:MAX_PAGES_PER_SUBJECT]

        sub_dir = out_dir / safe_name(subject)
        sub_dir.mkdir(parents=True, exist_ok=True)
        n_file, n_chars = 0, 0

        fail_streak = 0
        for title in titles:
            fname = safe_name(title.replace(f"{subject}/", "")) + ".txt"
            fp = sub_dir / fname
            # 断点续传：已抓到的文件不再重复下载，重跑只补缺失部分
            if fp.exists():
                n_file += 1
                n_chars += len(fp.read_text(encoding="utf-8"))
                sources.append((str(fp.relative_to(ROOT)),
                                "https://zh.wikibooks.org/wiki/" + urllib.parse.quote(title),
                                len(fp.read_text(encoding="utf-8"))))
                continue
            try:
                text = clean(get_extract(title))
                fail_streak = 0
            except Exception as e:      # noqa: BLE001
                print(f"  [跳过] {title}：{e}")
                fail_streak += 1
                if fail_streak >= 5:
                    print(f"  [{subject}] 连续失败 5 次，跳过该学科剩余页面")
                    break
                continue
            if len(text) < MIN_CHARS:
                continue
            key = hash(text[:500])
            if key in seen_content:      # 去重复内容
                continue
            seen_content.add(key)
            text = text[:MAX_CHARS_PER_PAGE]

            fp.write_text(text, encoding="utf-8")
            url = "https://zh.wikibooks.org/wiki/" + urllib.parse.quote(title)
            sources.append((str(fp.relative_to(ROOT)), url, len(text)))
            n_file += 1
            n_chars += len(text)
            time.sleep(0.3)              # 友好抓取间隔（网络不稳，放宽）

        summary.append((subject, n_file, n_chars))
        print(f"[{subject}] 抓到 {n_file} 个页面，共 {n_chars} 字")

    total_files = sum(s[1] for s in summary)
    total_chars = sum(s[2] for s in summary)

    # 语料清单
    (ROOT / "eval").mkdir(exist_ok=True)
    (ROOT / "eval" / "corpus.txt").write_text(
        "# 评测语料清单（每行一个文件，相对项目根目录）\n"
        + "\n".join(fp for fp, _u, _c in sources) + "\n",
        encoding="utf-8",
    )
    # 来源声明（论文附录）
    lines = [
        "# 语料来源与许可声明",
        "",
        "本评测语料抓取自 **中文维基教科书**（zh.wikibooks.org），",
        "内容采用 **CC BY-SA 3.0** 协议授权，允许在研究项目中自由使用与再分发（需署名）。",
        "",
        f"共 {total_files} 个文件，{total_chars} 字，覆盖 {len(sources)} 个页面。",
        "",
        "## 逐文件来源",
        "",
    ]
    for fp, url, c in sources:
        lines.append(f"- `{fp}`（{c} 字）— 来源：{url}")
    (ROOT / "eval" / "SOURCES.md").write_text("\n".join(lines), encoding="utf-8")

    print()
    print("=" * 60)
    print(f"合计：{total_files} 个文件，{total_chars} 字")
    print(f"语料清单：eval/corpus.txt")
    print(f"来源声明：eval/SOURCES.md（论文附录用）")
    if total_files < 20:
        print("[提示] 文件数偏少，建议扩大学科范围后再评测")
    print("=" * 60)


if __name__ == "__main__":
    main()
