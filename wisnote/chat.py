"""聊天记录：会话与消息（对应计划书"AI聊天记录保存"功能）

保存内容：每一轮用户提问 + AI 回答 + 引用来源(JSON) + 时间戳 + 会话ID。
支持：新建会话、按会话查看、搜索历史、继续对话、删除会话。
表结构见 wisnote/db.py 的 chat_sessions / chat_messages。
"""

import json
import re

from wisnote.config import HISTORY_MAX_CHARS, HISTORY_TURNS
from wisnote.db import get_conn


def create_session(user_id: int, title: str = "新会话") -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO chat_sessions (user_id, title) VALUES (?, ?)", (user_id, title)
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_sessions(user_id: int, include_empty: bool = False) -> list[dict]:
    """列出会话；默认过滤掉空会话（避免刷新页面产生的空会话淹没列表）"""
    sql = (
        "SELECT s.id, s.title, s.created_at, s.updated_at, "
        "       (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS msg_count "
        "FROM chat_sessions s WHERE s.user_id = ?"
    )
    if not include_empty:
        sql += " AND (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) > 0"
    sql += " ORDER BY s.updated_at DESC"
    conn = get_conn()
    try:
        rows = conn.execute(sql, (user_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_empty_sessions(user_id: int) -> int:
    """清理该用户的空会话（刷新页面误建的），返回删除数量"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM chat_sessions WHERE user_id = ? AND id NOT IN "
            "(SELECT DISTINCT session_id FROM chat_messages)",
            (user_id,),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def backfill_session_titles(user_id: int, default_titles: tuple = ("新会话", "网页会话")) -> int:
    """给早期没自动命名的会话补标题（用首条用户消息，不调用模型，速度快）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title FROM chat_sessions WHERE user_id = ?", (user_id,)
        ).fetchall()
        n = 0
        for s in rows:
            if s["title"] not in default_titles:
                continue
            first = conn.execute(
                "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user' "
                "ORDER BY id LIMIT 1",
                (s["id"],),
            ).fetchone()
            if not first:
                continue
            title = (first["content"] or "").strip().replace("\n", " ")[:20] or "新会话"
            conn.execute(
                "UPDATE chat_sessions SET title = ? WHERE id = ?", (title, s["id"])
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def get_session(session_id: int) -> dict | None:
    """读取单个会话信息（标题、摘要、时间）"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, title, context_summary, created_at, updated_at FROM chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def add_message(session_id: int, role: str, content: str, sources: list | None = None) -> int:
    """写入一条消息；sources 为溯源列表，自动存成 JSON"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, sources) VALUES (?, ?, ?, ?)",
            (
                session_id,
                role,
                content,
                json.dumps(sources, ensure_ascii=False) if sources is not None else None,
            ),
        )
        conn.execute(
            "UPDATE chat_sessions SET updated_at = datetime('now', 'localtime') WHERE id = ?",
            (session_id,),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_messages(session_id: int) -> list[dict]:
    """按时间顺序返回会话全部消息（sources 自动反序列化）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, role, content, sources, created_at FROM chat_messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("sources"):
            try:
                d["sources"] = json.loads(d["sources"])
            except json.JSONDecodeError:
                pass
        out.append(d)
    return out


def get_context_summary(session_id: int) -> str:
    """读取更早对话的压缩摘要（长对话记忆）"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT context_summary FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    return (row["context_summary"] or "") if row else ""


def update_context_summary(session_id: int, summary: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE chat_sessions SET context_summary = ? WHERE id = ?", (summary, session_id)
        )
        conn.commit()
    finally:
        conn.close()


def build_context_messages(
    session_id: int,
    max_turns: int = HISTORY_TURNS,
    max_chars: int = HISTORY_MAX_CHARS,
) -> list[dict]:
    """装配多轮上下文：历史摘要（若有）+ 最近若干条消息

    供 LLM 使用，格式与 system/user/assistant 一致。
    """
    msgs: list[dict] = []
    summary = get_context_summary(session_id)
    if summary:
        msgs.append(
            {"role": "system", "content": f"【更早对话要点回顾】\n{summary}"}
        )
    recent = get_messages(session_id)[-max_turns:]
    for m in recent:
        content = (m["content"] or "").strip()
        if not content:
            continue
        msgs.append({"role": m["role"], "content": content[:max_chars]})
    return msgs


def compress_history(session_id: int, keep_recent: int = HISTORY_TURNS) -> str | None:
    """把"较早的对话"压缩成摘要，实现长对话记忆

    只生成摘要，不删除原始消息——聊天记录仍需完整可回溯（计划书卖点）。
    """
    from wisnote.llm import chat

    msgs = get_messages(session_id)
    if len(msgs) <= keep_recent + 4:
        return None
    old = msgs[:-keep_recent]
    dialogue = "\n".join(
        f"{'用户' if m['role'] == 'user' else 'AI'}：{(m['content'] or '')[:300]}"
        for m in old
    )
    prompt = (
        "请用不超过 150 字概括下面这段对话，保留：用户关注的核心问题、已得出的结论、"
        "仍未解决的问题。只输出概括内容，不要加标题。\n\n" + dialogue
    )
    try:
        new_summary = chat(
            [{"role": "user", "content": prompt}], options={"num_predict": 256, "temperature": 0.2}
        )
    except Exception:
        return None
    merged = (get_context_summary(session_id) + "\n" + new_summary).strip()
    update_context_summary(session_id, merged)
    return merged


def auto_name_session(session_id: int, max_chars: int = 20) -> str:
    """根据第一轮问答自动生成会话标题（≤12字）

    优先用 Qwen 生成；模型不可用或输出异常时，退化为截取用户第一句话。
    """
    msgs = get_messages(session_id)
    first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
    if not first_user:
        return "新会话"

    first_ai = next((m["content"] for m in msgs if m["role"] == "assistant"), "")
    prompt = (
        "请为下面这段对话起一个简短标题，要求：不超过12个汉字、只输出标题本身、"
        "不要出现引号书名号冒号等符号、不要加解释。\n\n"
        f"用户提问：{first_user[:100]}\n助手回答：{first_ai[:100]}"
    )
    title = ""
    try:
        from wisnote.llm import chat
        title = chat(
            [{"role": "user", "content": prompt}],
            options={"num_predict": 24, "temperature": 0.2},
        )
    except Exception:
        title = ""

    # 清洗模型输出：去前缀、去引号、去掉首句之后的杂内容
    title = re.sub(r"^(标题|主题|会话标题)\s*[:：]\s*", "", (title or "").strip())
    title = re.sub(r'^[\s"“”\'‘’「」『』【】《》]*', "", title).strip()
    title = re.split(r"[\n。！？；;]", title)[0].strip()
    if not title or len(title) > 20:
        title = first_user.strip()[:max_chars]
    rename_session(session_id, title or "新会话")
    return title


def rename_session(session_id: int, title: str) -> None:
    conn = get_conn()
    try:
        conn.execute("UPDATE chat_sessions SET title = ? WHERE id = ?", (title, session_id))
        conn.commit()
    finally:
        conn.close()


def delete_session(session_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def search_history(user_id: int, keyword: str) -> list[dict]:
    """按关键词搜索历史问答（复习用）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT m.id, m.session_id, m.role, m.content, m.created_at, s.title AS session_title "
            "FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id "
            "WHERE s.user_id = ? AND m.content LIKE ? ORDER BY m.id DESC LIMIT 50",
            (user_id, f"%{keyword}%"),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
