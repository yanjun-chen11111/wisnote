"""社区问答（计划书模块4）：AI 初步回答 + 人工补充纠正 + 标记最佳答案

流程：用户提问 → 在社区公开笔记中检索 → Qwen 生成初步回答 →
其他用户人工补充/纠正 → 提问者标记最佳答案。
"""

import json

from wisnote.community import search_community
from wisnote.config import SEARCH_TOP_K
from wisnote.db import get_conn
from wisnote.llm import LLMError, chat

SYSTEM_PROMPT = """你是社区学习助手，基于社区公开笔记回答提问者的疑问。
规则：
1. 只能依据【社区笔记片段】回答，不得编造；
2. 用到片段处标注来源编号，如 [1]；
3. 片段不足以回答时，明确说明"社区笔记中没有足够信息"，并提示可以等待其他同学补充；
4. 语气友好，像同学之间互相讲解。"""


def ask_question(user_id: int, title: str, content: str = "", top_k: int = SEARCH_TOP_K) -> dict:
    """提问：写入问题，并立即用社区笔记生成 AI 初步回答"""
    title = (title or "").strip()
    if not title:
        raise ValueError("问题标题不能为空")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO questions (user_id, title, content) VALUES (?, ?, ?)",
            (user_id, title, content.strip()),
        )
        conn.commit()
        question_id = int(cur.lastrowid)
    finally:
        conn.close()

    hits = search_community(title if not content.strip() else f"{title} {content}", top_k)
    sources = [
        {
            "index": i,
            "document_id": h["document_id"],
            "title": h["title"],
            "owner_id": h["owner_id"],
            "page": h["page"],
            "para_index": h["para_index"],
            "similarity": round(h["similarity"], 4),
            "snippet": h["text"][:200],
        }
        for i, h in enumerate(hits, start=1)
    ]

    if not hits:
        answer_text = "社区笔记中暂时没有相关内容，等待其他同学补充解答。"
    else:
        context = "\n\n".join(
            f"[{i}] 来源：{h['title']}\n{h['text']}" for i, h in enumerate(hits, start=1)
        )
        try:
            answer_text = chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"【社区笔记片段】\n{context}\n\n【问题】\n{title}\n"
                                   f"{content}\n\n请回答并标注来源编号。",
                    },
                ]
            )
        except LLMError as e:
            answer_text = f"（AI 回答生成失败：{e}）请等待人工补充。"

    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO answers (question_id, user_id, content, is_ai, sources) "
            "VALUES (?, NULL, ?, 1, ?)",
            (question_id, answer_text, json.dumps(sources, ensure_ascii=False)),
        )
        conn.commit()
        answer_id = int(cur.lastrowid)
    finally:
        conn.close()

    return {"question_id": question_id, "answer_id": answer_id, "ai_answer": answer_text, "sources": sources}


def add_answer(user_id: int, question_id: int, content: str) -> int:
    """人工补充 / 纠正回答"""
    content = (content or "").strip()
    if not content:
        raise ValueError("回答内容不能为空")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO answers (question_id, user_id, content, is_ai) VALUES (?, ?, ?, 0)",
            (question_id, user_id, content),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def mark_best_answer(user_id: int, question_id: int, answer_id: int) -> None:
    """提问者标记最佳答案"""
    conn = get_conn()
    try:
        q = conn.execute("SELECT user_id FROM questions WHERE id = ?", (question_id,)).fetchone()
        if q is None:
            raise ValueError("问题不存在")
        if q["user_id"] != user_id:
            raise ValueError("只有提问者可以标记最佳答案")
        conn.execute("UPDATE questions SET best_answer_id = ? WHERE id = ?", (answer_id, question_id))
        conn.commit()
    finally:
        conn.close()


def list_questions(limit: int = 50) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT q.id, q.title, q.content, q.created_at, q.best_answer_id, "
            "       u.username AS asker, "
            "       (SELECT COUNT(*) FROM answers a WHERE a.question_id = q.id) AS answer_count "
            "FROM questions q JOIN users u ON u.id = q.user_id "
            "ORDER BY q.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_question(question_id: int) -> dict:
    """问题详情 + 全部回答（AI 回答在前，最佳答案置顶显示）"""
    conn = get_conn()
    try:
        q = conn.execute(
            "SELECT q.id, q.title, q.content, q.created_at, q.best_answer_id, u.username AS asker, q.user_id "
            "FROM questions q JOIN users u ON u.id = q.user_id WHERE q.id = ?",
            (question_id,),
        ).fetchone()
        if q is None:
            raise ValueError("问题不存在")
        rows = conn.execute(
            "SELECT a.id, a.content, a.is_ai, a.sources, a.created_at, "
            "       CASE WHEN a.is_ai = 1 THEN 'AI 助手' ELSE u.username END AS author "
            "FROM answers a LEFT JOIN users u ON u.id = a.user_id "
            "WHERE a.question_id = ? ORDER BY a.id",
            (question_id,),
        ).fetchall()
    finally:
        conn.close()
    out = dict(q)
    answers = []
    for r in rows:
        d = dict(r)
        if d.get("sources"):
            try:
                d["sources"] = json.loads(d["sources"])
            except json.JSONDecodeError:
                pass
        answers.append(d)
    # 最佳答案置顶
    if out["best_answer_id"]:
        answers.sort(key=lambda a: a["id"] != out["best_answer_id"])
    out["answers"] = answers
    return out
