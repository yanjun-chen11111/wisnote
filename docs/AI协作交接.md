# AI 协作交接文档（新会话必读）

> 用途：每次新开 AI 会话（换窗口/换工具）时，让 AI 先读本文件 + `git log --oneline -20`，
> 即可恢复完整上下文。本文件由 AI 在每次重要节点后更新。

## 项目定位

WisNote：RAG 个人笔记学习平台。Streamlit UI + 本地 Ollama（Qwen3-1.7B，CPU）+
bge-small-zh 嵌入 + ChromaDB + SQLite。核心卖点：本地、免费、离线、答案可溯源。

## 架构速览（关键文件）

```
上传 → wisnote/parsers/（5类解析器+OCR fallback）→ chunker(500/50) → ingest
     → SQLite(chunks) + Chroma(向量)
提问 → search.py → hybrid.py（BM25+向量 RRF融合）→ rag.py（闸门→生成→引用增强）
     → llm.py（Ollama HTTP，流式+think过滤）
讲解 → explain.py（三模式：依据笔记/自由/自动）
评测 → scripts/eval_retrieval.py(小语料三路) · eval_bench.py(公开基准大规模)
     · exp_params.py(参数网格) · fetch_bench_corpus.py(T2Retrieval取数)
```

## 已验证的关键决策（不要推翻，除非有新证据）

1. **混合检索（RRF）**：语义+BM25 融合，闸门与排序职责分离。
   大规模基准（T2Retrieval 600真实查询×2万段，4.5万块）实测混合全面第一
   （R@1 87.2% > 语义 85.8% > BM25 80.2%），小语料上 BM25 反超是假象；
   闸门（拒答）只看最高语义相似度，BM25 分数不参与。
2. **引用位置由程序写，不由模型写**：实测 1.7B 模型会编造页码。
   模型只标 [n]，`rag.py enrich_citations`（含流式版）确定性替换真实位置。
3. **流式输出曾从未生效**：`llm.py _clean_stream` 曾把内容攒到流结束一次性
   yield（E2E 实测 chunk数=1 暴露）。已修为逐段透传+防分裂尾巴。
   教训：声称任何系统行为前必须实测。
4. **OCR fallback**：扫描版PDF逐页渲染、图片型PPT取内嵌图、docx嵌图OCR，
   共用 `image_parser.ocr_image_bytes()`。页眉页脚刻意跳过（噪声）。
5. **评测分层**：公开基准测架构、自己的数据调参数、手写goldset做验收，
   一次只动一层（见 docs/检索评测分层方法论.md）。

## 验证约定（每个改动都要走）

```powershell
.venv\Scripts\python.exe -m pytest -q                      # 全量（78个）
.venv\Scripts\python.exe -m ruff check <改动文件> --fix
.venv\Scripts\python.exe -m bandit -r wisnote scripts -q --skip B310,B608
```
- **功能变化必须同步更新根目录 `CHANGELOG.md`**（按功能类别追加一条，格式见文件头）
- 模型行为主张必须 E2E 实测（测 chunk 数、测位置真伪，不测不下结论）
- 文档同步：改行为必须同步 docs/ 对应文档
- PowerShell 内联 python 引号会被吞，长脚本写临时文件到 C:/temp/

## 当前状态（2026-10-13）

已完成：混合检索上线✅ 大规模基准评测✅ OCR fallback✅ docx全量提取✅
流式修复✅ 引用位置增强✅ 提示词结构化展开✅ 测试78/78✅

## 待办

1. **git push**（用户自己在终端做一次浏览器 OAuth，之后免密）——本地多个提交待推
2. A方案合成题集（本地Qwen对自己笔记出题）→ 参数网格寻优
3. explain.py 的引用增强尚未同步 enrich_citations（问答已做，讲解未做）
4. 待办功能：课程重命名/合并/删除、局域网/Docker部署、问答UX打磨
5. bench 语料（4.5万块）留在 data/ 供复评，磁盘紧张可清

## 环境事实

- Windows + PowerShell；venv 在 `.venv`，一律用 `.venv\Scripts\python.exe`
- Ollama: http://127.0.0.1:11434，qwen3:1.7b，常驻内存
- HuggingFace 走 hf-mirror.com（vectorstore/fetch 脚本内已设 HF_ENDPOINT）
- bench/eval 测试账号在真实库 data/wisnote.db（用户数据勿动）
- picture/ 目录是用户个人图片，勿入库勿删
