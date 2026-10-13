# WisNote 智能笔记学习平台

基于 RAG 的个人笔记助手：支持笔记检索、问答、答案溯源，以及社区/榜单等学习化功能。
前端用 Streamlit，后端为 Python 笔记处理与检索服务。

## 目录结构

- `wisnote/`：RAG 笔记后端（检索 / 问答 / 解析 / 社区）
- `ui/`：Streamlit 前端（会话 Cookie 登录）
- `docs/`：设计文档（会话安全、检索评测、问答模式等）
- `.codebuddy/skills/engineering-coach/`：内置「工程教练」协作技能
- `.codebuddy/skills/test-pilot/`：内置「工程级测试」技能

## 技术栈

- 后端：Python
- 前端：Streamlit
- 技能：CodeBuddy 工程教练（`engineering-coach`）+ 工程级测试（`test-pilot`）

## 快速开始（本地运行）

```powershell
python -m venv .venv
.venv\Scripts\activate          # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run ui/app.py
```

## 运行测试

```powershell
pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/ -q --cov=wisnote.ingest --cov=wisnote.explain --cov=wisnote.parsers.image_parser --cov-report=term-missing
```

测试自动使用临时数据库与临时向量库，不会触碰 `data/` 下的真实数据。

最新一轮的可视化报告（用例明细 + 逐行覆盖率）见 [`docs/test-report/index.html`](docs/test-report/index.html)，
本地用浏览器直接打开即可。

## 内置技能（engineering-coach + test-pilot）

本仓库在 `.codebuddy/skills/` 下内置两个 CodeBuddy 技能，克隆后即**自动生效**，无需安装：

- **engineering-coach（工程教练）**：需求 / 想法阶段，把非工程化诉求翻译成工程问题、讲清取舍、锚定业界做法；
  也作为「导师」先对齐需求再动手。技能说明见其 `references/examples.md`。
- **test-pilot（工程级测试）**：实现 / 验证阶段，按规格推导测试用例、用五大设计技术 + 分层覆盖、自适应项目框架、强制自检漏测。

两者配合：先用 engineering-coach 把需求聊清楚、定方案，再用 test-pilot 把功能测到位。
完整用法见 **[`docs/技能使用指南.md`](docs/技能使用指南.md)**。

两个技能也都独立发布，可单独分发：
- engineering-coach: `github.com/yanjun-chen11111/codebuddy-engineering-coach`
- test-pilot: `github.com/yanjun-chen11111/codebuddy-test-pilot`

## 协作者指南

### 1. 网络代理（必看，否则连不上 GitHub）

如果本机使用系统代理（如 Clash / V2Ray），**git 默认不会读取系统代理**，
直接 `git pull` / `git push` 会报：

```
fatal: unable to access 'https://github.com/...': Could not connect to server
```

需要把代理写进 git 全局配置（**一次即可，之后所有 git 命令自动走代理**，不用每次设环境变量）：

```powershell
git config --global http.proxy  http://127.0.0.1:7897
git config --global https.proxy http://127.0.0.1:7897
```

> 端口 `7897` 是开发机 Clash 的默认端口，请替换为你自己的代理端口。
> 取消代理：`git config --global --unset http.proxy` 与 `git config --global --unset https.proxy`。

### 2. 更新流程

```powershell
cd "d:/all projects/wisnote"
git checkout main
git pull
# 修改代码……
git add .
git commit -m "你的改动说明"
git push
```

首次 `git push` 会弹出 GitHub 登录框，用 **Personal Access Token（PAT）** 当作密码登录即可；
Git Credential Manager 会记住凭据，之后推送免输。

### 3. 单独维护技能仓库

项目的技能以子目录形式随本仓库一起提交；若你想单独维护/发布技能，
请克隆对应的独立技能仓库再提交：engineering-coach `github.com/yanjun-chen11111/codebuddy-engineering-coach`，
test-pilot `github.com/yanjun-chen11111/codebuddy-test-pilot`。
