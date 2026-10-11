"""AI 摘要与知识点标签（计划书模块2/3）

上传笔记后自动生成：200 字以内摘要 + 3~5 个知识点标签。
仍由同一个 Qwen3-1.7B 通过 Prompt 路由完成，不额外加载模型。

输出采用"摘要：/ 标签："的固定格式（比让小模型输出 JSON 更稳定），
再做正则解析；解析失败时降级为截取前 200 字，保证功能不中断。
"""

import re

from wisnote.db import get_conn
from wisnote.llm import chat

SYSTEM_PROMPT = "你是笔记整理助手，擅长提炼核心内容与知识点标签。"

USER_TEMPLATE = """下面是某课程笔记的部分内容，请你完成两项任务：
1. 用不超过 200 字概括核心内容，写成一段通顺的话；
2. 提取 3~5 个知识点标签，每个 2~6 字，用顿号分隔。

【笔记内容】
{text}

严格按下面格式输出，不要输出任何其他内容：
摘要：xxx
标签：a、b、c"""

MAX_INPUT_CHARS = 3000     # 小模型上下文有限，只喂前 3000 字
MAX_SUMMARY_CHARS = 200


def get_document_text(user_id: int, document_id: int, max_chars: int = MAX_INPUT_CHARS) -> str:
    """取资料的切块文本（按顺序拼接，截断）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT text FROM chunks WHERE document_id = ? AND user_id = ? ORDER BY chunk_index",
            (document_id, user_id),
        ).fetchall()
    finally:
        conn.close()
    text = "\n".join(r["text"] for r in rows)
    return text[:max_chars]


def parse_output(raw: str) -> tuple[str, list[str]]:
    """解析"摘要：/ 标签："格式输出"""
    raw = (raw or "").strip()
    m = re.search(r"摘要[:：]\s*(.*?)\s*\n+\s*标签[:：]\s*(.*)", raw, re.DOTALL)
    if not m:
        # 降级：整段当摘要，无标签
        return raw[:MAX_SUMMARY_CHARS], []
    summary = m.group(1).strip()[:MAX_SUMMARY_CHARS]
    tags_raw = m.group(2).strip()
    tags = [t.strip() for t in re.split(r"[、,，;；/|]", tags_raw) if t.strip()]
    return summary, tags[:5]


def summarize_text(text: str) -> dict:
    """对任意文本生成摘要与标签"""
    text = (text or "").strip()
    if not text:
        return {"summary": "", "tags": [], "raw": ""}
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
    raw = chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(text=text)},
        ],
        options={"num_predict": 256, "temperature": 0.2},
    )
    summary, tags = parse_output(raw)
    return {"summary": summary, "tags": tags, "raw": raw}


def summarize_document(user_id: int, document_id: int, persist: bool = True) -> dict:
    """为一份资料生成摘要与标签，并写回数据库"""
    text = get_document_text(user_id, document_id)
    if not text:
        return {"document_id": document_id, "summary": "", "tags": [], "error": "该资料没有可摘要的文本"}

    result = summarize_text(text)
    result["document_id"] = document_id

    if persist:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE documents SET summary = ?, tags = ? WHERE id = ? AND user_id = ?",
                (result["summary"], "、".join(result["tags"]), document_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()
    return result


def get_summary(user_id: int, document_id: int) -> dict:
    """读取已保存的摘要与标签"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT summary, tags FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"summary": "", "tags": []}
    tags = [t.strip() for t in (row["tags"] or "").split("、") if t.strip()]
    return {"summary": row["summary"] or "", "tags": tags}
