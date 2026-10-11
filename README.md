# WisNote 智能笔记学习平台

基于 RAG 的个人笔记助手：支持笔记检索、问答、答案溯源，以及社区/榜单等学习化功能。
前端用 Streamlit，后端为 Python 笔记处理与检索服务。

## 目录结构

- `wisnote/`：RAG 笔记后端（检索 / 问答 / 解析 / 社区）
- `ui/`：Streamlit 前端（会话 Cookie 登录）
- `docs/`：设计文档（会话安全、检索评测、问答模式等）
- `.codebuddy/skills/engineering-coach/`：内置的「工程教练」协作技能

## 技术栈

- 后端：Python
- 前端：Streamlit
- 技能：CodeBuddy 工程教练技能（`engineering-coach`）

## 快速开始（本地运行）

```powershell
python -m venv .venv
.venv\Scripts\activate          # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run ui/app.py
```

## 内置技能：engineering-coach（工程教练）

本仓库在 `.codebuddy/skills/engineering-coach/` 内置了一个 CodeBuddy 技能。
克隆仓库后即可在对话中**自动生效**，无需额外安装。

它的作用是：把没有工程背景的产品想法翻译成工程问题，参照业界成熟做法给方案、
讲清取舍，并说明该问题在真实公司开发中是否典型、成熟团队怎么解——目标是做出
**生产级产品**而非玩具。技能说明见 `references/examples.md`。

该技能也独立发布在 `github.com/yanjun-chen11111/codebuddy-engineering-coach`，
可单独下载使用。

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
请克隆独立的技能仓库 `github.com/yanjun-chen11111/codebuddy-engineering-coach` 再提交。
