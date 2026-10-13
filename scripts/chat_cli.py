"""命令行 AI 问答（第4周功能验证）：提问 → 检索 → Qwen 回答 → 溯源 → 自动保存记录

运行方式（项目根目录，激活 venv 后）：
    python scripts/chat_cli.py

命令：
    直接输入问题   → AI 基于你的笔记回答（附溯源编号）
    /explain       → AI 知识点讲解（四模式：通俗/分步/引导/考点）
    /docs          → 查看已上传资料
    /upload        → 上传资料
    /sessions      → 查看历史会话
    /open <编号>   → 打开某会话继续对话
    /new           → 新建会话
    /hist          → 显示当前会话历史
    /find <关键词> → 搜索历史问答
    /exit          → 退出
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wisnote import auth
from wisnote.chat import create_session, get_messages, list_sessions, search_history
from wisnote.config import SUPPORTED_EXTS
from wisnote.db import init_db
from wisnote.ingest import ingest_document, list_documents
from wisnote.explain import EXPLAIN_MODES, list_modes, stream_explain
from wisnote.llm import LLMError, has_model, ping, warmup
from wisnote.rag import ask, format_answer

init_db()

user: dict | None = None
session_id: int | None = None


def require_login() -> int:
    if user is None:
        print("请先登录（输入 /login）")
        raise SystemExit(1)
    return user["user_id"]


def do_login() -> None:
    global user, session_id
    username = input("用户名：").strip()
    password = input("密码：").strip()
    try:
        user = auth.login(username, password)
    except auth.AuthError:
        print("用户不存在或密码错误，是否注册？(y/n)")
        if input().strip().lower() == "y":
            auth.register(username, password)
            user = auth.login(username, password)
        else:
            return
    session_id = None
    print(f"欢迎，{user['username']}")


def ensure_session() -> int:
    global session_id
    if session_id is None:
        session_id = create_session(user["user_id"], "命令行会话")
    return session_id


def do_upload() -> None:
    uid = require_login()
    path = input("文件路径：").strip().strip('"').strip("'")
    if Path(path).suffix.lower() not in SUPPORTED_EXTS:
        print(f"不支持的类型，支持：{'/'.join(SUPPORTED_EXTS)}")
        return
    course = input("课程分类（可留空）：").strip() or None
    try:
        r = ingest_document(uid, path, course_name=course)
        print(f"入库成功：{r['title']}（段落 {r['blocks']} → 块 {r['chunks']}）")
    except Exception as e:
        print(f"入库失败：{e}")


def do_ask(question: str) -> None:
    uid = require_login()
    sid = ensure_session()
    print("\n正在检索并生成回答（CPU 首次推理约 10~30 秒）...")
    try:
        result = ask(uid, question, session_id=sid)
    except LLMError as e:
        print(f"模型调用失败：{e}")
        return
    print()
    print(format_answer(result))
    print()


def do_explain() -> None:
    require_login()
    topic = input("要讲解的知识点：").strip()
    if not topic:
        return
    print("讲解模式：")
    for i, (key, label) in enumerate(list_modes(), start=1):
        print(f"  {i}. {label}（{key}）")
    pick = input("选择（默认1）：").strip() or "1"
    keys = list_modes()
    try:
        mode = keys[int(pick) - 1][0]
    except (ValueError, IndexError):
        mode = keys[0][0]
    source_labels = {
        "auto": "笔记优先（有笔记结合讲，没有则用模型知识）",
        "notes": "只用笔记（最严谨，可溯源）",
        "llm": "只用模型知识（不查笔记）",
    }
    print("讲解依据：")
    for i, key in enumerate(source_labels, start=1):
        print(f"  {i}. {source_labels[key]}（{key}）")
    spick = input("选择（默认1）：").strip() or "1"
    try:
        source = list(source_labels)[int(spick) - 1]
    except (ValueError, IndexError):
        source = "auto"
    print(f"\n[{EXPLAIN_MODES[mode]['label']}] 正在生成讲解（CPU 约 20~60 秒）...")
    holder: dict = {}
    try:
        parts = []
        for chunk in stream_explain(
            user["user_id"], topic, mode=mode,
            session_id=ensure_session(), result_holder=holder, source=source,
        ):
            parts.append(chunk)
            print(chunk, end="", flush=True)
        print()
    except LLMError as e:
        print(f"\n模型调用失败：{e}")
        return
    print()
    print(format_answer({"answer": "", "sources": holder.get("sources", [])}))
    print()


def do_sessions() -> None:
    uid = require_login()
    rows = list_sessions(uid)
    if not rows:
        print("（暂无会话）")
        return
    for s in rows:
        print(f"#{s['id']} {s['title']}  消息 {s['msg_count']} 条  更新于 {s['updated_at']}")


def do_open(arg: str) -> None:
    global session_id
    require_login()
    try:
        session_id = int(arg)
    except ValueError:
        print("用法：/open <会话编号>")
        return
    msgs = get_messages(session_id)
    print(f"已打开会话 #{session_id}（{len(msgs)} 条消息）")
    for m in msgs[-10:]:
        who = "你" if m["role"] == "user" else "AI"
        print(f"  [{who}] {m['content'][:80]}")


def do_hist() -> None:
    if session_id is None:
        print("（当前没有会话）")
        return
    for m in get_messages(session_id):
        who = "你" if m["role"] == "user" else "AI"
        print(f"[{who}] {m['content']}")
        if m.get("sources"):
            for s in m["sources"]:
                loc = f"第{s['page']}页" if s["page"] else "无页码"
                print(f"    引用[{s['index']}] {s['title']} | {loc}")
        print()


def do_find(kw: str) -> None:
    uid = require_login()
    rows = search_history(uid, kw)
    if not rows:
        print("（没有匹配的历史记录）")
        return
    for r in rows:
        who = "你" if r["role"] == "user" else "AI"
        print(f"#{r['session_id']} [{who}] {r['content'][:80]}  ({r['created_at']})")


def main() -> None:
    print("=" * 60)
    print("WisNote 命令行：AI 问答 + 溯源 + 聊天记录")
    print("=" * 60)
    if not ping():
        print("[警告] 连不上 Ollama，请先启动 Ollama（ollama serve）")
    elif not has_model():
        print("[警告] 未检测到 Qwen3-1.7B，请执行：ollama pull qwen3:1.7b")
    else:
        print("[就绪] Ollama 与 Qwen3-1.7B 已就绪，正在预热模型（约10秒）...")
        try:
            warmup()
            print("[就绪] 模型已常驻内存")
        except LLMError as e:
            print(f"[警告] 预热失败：{e}")
    print()

    while True:
        raw = input("你> ").strip()
        if not raw:
            continue
        if raw in ("/exit", "/quit"):
            print("再见！")
            return
        if raw == "/login":
            do_login()
        elif raw == "/docs":
            for d in list_documents(require_login()):
                print(f"#{d['id']} [{d['file_type']}] {d['title']}  课程:{d['course_name'] or '-'}")
        elif raw == "/upload":
            do_upload()
        elif raw == "/explain":
            do_explain()
        elif raw == "/sessions":
            do_sessions()
        elif raw.startswith("/open"):
            do_open(raw[5:].strip())
        elif raw == "/new":
            user["session_id"] = None
            ensure_session()
            print("已新建会话")
        elif raw == "/hist":
            do_hist()
        elif raw.startswith("/find"):
            do_find(raw[5:].strip())
        elif raw.startswith("/"):
            print("未知命令")
        else:
            if user is None:
                print("请先登录（/login）")
                continue
            do_ask(raw)


if __name__ == "__main__":
    main()
