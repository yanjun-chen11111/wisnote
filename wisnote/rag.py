"""RAG 问答：检索 → 组装 Prompt → Qwen 生成 → 回答 + 溯源

核心原则（计划书模块1）：
1. 回答必须严格基于检索到的笔记片段，不得编造；
2. 每处引用用 [1][2] 编号标注，可追溯到"哪个文件、第几页、第几段"；
3. 检索不到相关内容时直接说明，不猜测（防幻觉）。
"""


import re

from wisnote.chat import auto_name_session, build_context_messages, get_messages
from wisnote.config import AUTO_COMPRESS_THRESHOLD, SEARCH_TOP_K
from wisnote.llm import chat, chat_stream
from wisnote.search import search_notes

SYSTEM_PROMPT = """你是"智能笔记助手"，只根据用户自己的笔记内容回答问题，像一个耐心的学霸同学给同学讲解。
严格遵守以下规则：
1. 只能使用【笔记片段】中的信息，不得使用外部知识，不得编造；
2. 引用笔记内容的地方，只标注方括号编号，如：进程调度算法包括……[1]；
   **不要自己写页码、段落号或文件名**（系统会在编号后自动补全真实位置，你写了反而会出错）；
3. 转述关键原文时用"原文：'……'"标出，便于读者去笔记核对；
4. 如果所有片段都无法回答问题，必须回答："笔记中没有相关内容，我无法据此回答。"然后说明还缺什么资料；
5. 回答要**完整展开，不要压缩成一句话**。按下面的骨架组织（片段里有的部分才写，没有的跳过）：
   - 先用一两句话直接给出结论或定义；
   - 再逐条展开关键点：条件、性质、分类、步骤、原理，每条都来自笔记原文；
   - 笔记里有例子或对比的一并转述；
   - 每个小节写完就停，**不要重复已经写过的内容**；
6. 能分点就分点，用中文。"""

NO_CONTEXT_ANSWER = "笔记中没有相关内容，我无法据此回答。请先上传包含该知识点的笔记。"


def build_context(hits: list[dict]) -> str:
    """把检索结果组装成带编号和位置信息的上下文"""
    parts = []
    for i, h in enumerate(hits, start=1):
        loc = f"第{h['page']}页" if h.get("page") else "无页码"
        loc += f" 第{h.get('para_index')}段" if h.get("para_index") is not None else ""
        parts.append(f"[{i}] 来源：{h.get('title', '未知文档')} | {loc}\n{h['text']}")
    return "\n\n".join(parts)


def build_sources(hits: list[dict]) -> list[dict]:
    """溯源信息（供界面展示与聊天记录保存）"""
    return [
        {
            "index": i,
            "document_id": h.get("document_id"),
            "title": h.get("title", ""),
            "page": h.get("page"),
            "para_index": h.get("para_index"),
            "similarity": round(h.get("similarity", 0.0), 4),
            "snippet": h.get("text", "")[:200],
        }
        for i, h in enumerate(hits, start=1)
    ]


def build_messages(question: str, hits: list[dict]) -> list[dict]:
    context = build_context(hits)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"【笔记片段】\n{context}\n\n【问题】\n{question}\n\n"
                       f"请基于上述笔记片段完整回答（结构化展开、细节不压缩），"
                       f"引用处标注来源编号及位置（照抄片段头部来源行）。",
        },
    ]


_CITE_RE = re.compile(r"\[(\d+)\]")


def cite_label(s: dict) -> str:
    """来源编号 → 带真实位置的完整引用标签（程序生成，杜绝模型编造页码）"""
    loc = f"第{s['page']}页" if s.get("page") else "无页码"
    if s.get("para_index") is not None:
        loc += f" 第{s['para_index']}段"
    return f"[{s['index']}]《{s['title']}》{loc}"


def enrich_citations(text: str, sources: list[dict]) -> str:
    """把回答中的 [n] 替换为带真实位置的完整引用（确定性替换，零幻觉）

    为什么不让模型自己写位置：E2E 实测 1.7B 模型会编造页码
    （真实来源"无页码 第1段"，模型写出"第12页 第3段"），
    编造的引用位置比没有位置更误导用户。
    """
    by_idx = {s["index"]: s for s in sources}
    return _CITE_RE.sub(
        lambda m: cite_label(by_idx[int(m.group(1))]) if int(m.group(1)) in by_idx else m.group(0),
        text,
    )


def _enrich_stream(chunks, sources: list[dict]):
    """流式版引用增强：编号可能被拆在 chunk 交界，按最后一个"["切保留区

    修复记录：第一版无条件保留末尾 4 字符，编号横跨刷新边界时
    半个标记（"["）已被吐出、永远无法匹配（实测 yield 出 '条件['）。
    正确规则：完整编号立即替换；未闭合的"["及其后内容保留到下一 chunk。
    """
    by_idx = {s["index"]: s for s in sources}
    buf = ""
    for c in chunks:
        buf += c
        while True:
            m = _CITE_RE.search(buf)
            if not m:
                break
            yield buf[: m.start()]
            n = int(m.group(1))
            yield cite_label(by_idx[n]) if n in by_idx else m.group(0)
            buf = buf[m.end():]
        # 从最后一个"["开始的内容可能是半截编号，保留到下个 chunk 再判定
        cut = buf.rfind("[")
        if cut == -1:
            if buf:
                yield buf
            buf = ""
        else:
            yield buf[:cut]
            buf = buf[cut:]
    if buf:
        yield enrich_citations(buf, sources)


def max_semantic_similarity(hits: list[dict]) -> float:
    """全部命中中的最高语义相似度（防幻觉闸门专用）

    混合检索的排序依据是 RRF 融合分，Top1 未必是语义上最相关的 chunk，
    闸门必须取全部命中的最高语义相似度，保持原有阈值语义不变；
    仅 BM25 命中的 chunk（无语义相似度）计为 0.0，不会抬升闸门。
    """
    return max((h.get("similarity", 0.0) for h in hits), default=0.0)


def ask(
    user_id: int,
    question: str,
    top_k: int = SEARCH_TOP_K,
    session_id: int | None = None,
    save: bool = True,
    min_similarity: float = 0.35,
) -> dict:
    """提问并得到回答（含溯源）

    返回：{"question", "answer", "sources", "hits", "from_llm"}
    - min_similarity：最高相似度低于此值视为"笔记里没有"，直接拒答（防幻觉闸门）
    - session_id：传入则自动把问答写入聊天记录
    """
    question = question.strip()
    if not question:
        raise ValueError("问题不能为空")

    hits = search_notes(user_id, question, top_k)
    sources = build_sources(hits)

    # 防幻觉闸门：检索不到、或最高语义相似度过低，不交给大模型
    if not hits or (max_semantic_similarity(hits) < min_similarity):
        result = {
            "question": question,
            "answer": NO_CONTEXT_ANSWER,
            "sources": [],
            "hits": hits,
            "from_llm": False,
        }
        if session_id and save:
            from wisnote.chat import add_message
            add_message(session_id, "user", question)
            add_message(session_id, "assistant", result["answer"], sources=[])
        return result

    # 记录资料被引用次数（计划书模块2 的元数据要求）
    from wisnote.ingest import bump_ref_counts
    bump_ref_counts({h.get("document_id") for h in hits})

    answer = enrich_citations(chat(build_messages(question, hits)), sources)
    result = {
        "question": question,
        "answer": answer,
        "sources": sources,
        "hits": hits,
        "from_llm": True,
    }
    if session_id and save:
        from wisnote.chat import add_message
        add_message(session_id, "user", question)
        add_message(session_id, "assistant", answer, sources=sources)
    return result


FREE_SYSTEM_PROMPT = """你是一个耐心、专业的 AI 学习助手，可以自由回答各类学习问题。
要求：
1. 回答准确、条理清晰，能分点就分点，必要时举例说明；
2. 涉及计算或推导时写出关键步骤，不要只给结论；
3. 如果你对某个知识点没有把握，诚实说明"这部分我不确定"，不要编造；
4. 用中文回答。"""


def _save_turn(session_id: int | None, question: str, answer: str, sources: list | None) -> None:
    """把一轮问答写入聊天记录"""
    if not session_id:
        return
    from wisnote.chat import add_message
    add_message(session_id, "user", question)
    add_message(session_id, "assistant", answer, sources=sources or [])


def free_ask(
    user_id: int,
    question: str,
    session_id: int | None = None,
    save: bool = True,
    max_history: int = 6,
    max_chars_per_msg: int = 600,
) -> dict:
    """自由对话：不依赖笔记资料，带多轮上下文

    上下文只取最近 max_history 条消息且每条截断，避免 CPU 上提示过长导致变慢。
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    messages = [{"role": "system", "content": FREE_SYSTEM_PROMPT}]
    if session_id:
        from wisnote.chat import get_messages
        for m in get_messages(session_id)[-max_history:]:
            messages.append(
                {"role": m["role"], "content": (m["content"] or "")[:max_chars_per_msg]}
            )
    messages.append({"role": "user", "content": question})

    answer = chat(messages, options={"num_predict": 768})
    result = {
        "question": question,
        "answer": answer,
        "sources": [],
        "hits": [],
        "from_llm": True,
        "mode": "free",
    }
    if save:
        _save_turn(session_id, question, answer, None)
    return result


def auto_ask(
    user_id: int,
    question: str,
    top_k: int = SEARCH_TOP_K,
    session_id: int | None = None,
    save: bool = True,
    min_similarity: float = 0.35,
) -> dict:
    """智能模式：先检索，资料命中就基于资料回答（附溯源），否则自动转为自由对话"""
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    hits = search_notes(user_id, question, top_k)
    if hits and max_semantic_similarity(hits) >= min_similarity:
        from wisnote.ingest import bump_ref_counts
        bump_ref_counts({h.get("document_id") for h in hits})
        answer = chat(build_messages(question, hits))
        sources = build_sources(hits)
        result = {
            "question": question,
            "answer": answer,
            "sources": sources,
            "hits": hits,
            "from_llm": True,
            "mode": "notes",
        }
        if save:
            _save_turn(session_id, question, answer, sources)
        return result

    result = free_ask(user_id, question, session_id=session_id, save=save)
    result["mode"] = "free"
    result["note"] = "笔记中未找到相关内容，已转为通用回答（无溯源）"
    return result


# 指代词：出现这些词且句子很短时，判定为"追问"，需要查询重写
_PRONOUNS = ("它", "他", "这个", "那个", "这", "那", "上面", "刚才", "继续", "还有", "那么", "呢")


def needs_context(question: str) -> bool:
    """判断问题是否依赖上文（短句或含指代词）"""
    q = (question or "").strip()
    return len(q) <= 12 or any(p in q for p in _PRONOUNS)


def rewrite_query(question: str, session_id: int | None) -> str:
    """查询重写：把上一轮问题拼进检索词，解决"它/这个"指代导致检索不到的问题

    只影响**检索用词**，不把对话历史交给生成模型 → 保证回答严格基于本轮资料、溯源无歧义。
    """
    if not session_id or not needs_context(question):
        return question
    try:
        from wisnote.chat import get_messages
        prev = next(
            (m["content"] for m in reversed(get_messages(session_id)) if m["role"] == "user"),
            None,
        )
    except Exception:
        prev = None
    if not prev or prev == question:
        return question
    return f"{prev[:80]} {question}"


def stream_ask(
    user_id: int,
    question: str,
    mode: str = "auto",
    session_id: int | None = None,
    save: bool = True,
    min_similarity: float = 0.35,
    result_holder: dict | None = None,
    use_history: bool = True,
):
    """流式问答：检索先完成，然后逐字产出回答；结束后自动写入聊天记录

    mode: notes=只依据资料 / auto=有资料用资料否则自由回答 / free=自由对话
    result_holder: 传入一个 dict，流式结束后可从中取 sources / mode / hits
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    # 纯单轮模式（use_history=False）：直接用原问题检索，不参考任何历史
    # 仅当开启"带入上下文"时才做查询补全
    search_query = rewrite_query(question, session_id) if use_history else question
    hits = search_notes(user_id, search_query, SEARCH_TOP_K) if mode in ("notes", "auto") else []

    if mode == "notes":
        if not hits or max_semantic_similarity(hits) < min_similarity:
            if result_holder is not None:
                result_holder.update({"sources": [], "mode": "notes", "hits": hits, "refused": True})
            yield NO_CONTEXT_ANSWER
            if save:
                _save_turn(session_id, question, NO_CONTEXT_ANSWER, None)
            return
        use_notes = True
    elif mode == "auto":
        use_notes = bool(hits) and max_semantic_similarity(hits) >= min_similarity
    else:
        use_notes = False

    # 记录进入本轮前的消息数，用于判断是否为第一轮（决定是否自动命名）
    prior_msg_count = len(get_messages(session_id)) if session_id else 0

    # 多轮上下文：历史摘要 + 最近若干条消息（use_history=False 时完全不带，保证单轮可复现）
    history_messages = (
        build_context_messages(session_id) if (session_id and use_history) else []
    )
    # 追问消解：带上上一轮的问题，帮助小模型理解"它/这个/那"指什么
    last_question = next(
        (m["content"] for m in reversed(history_messages) if m["role"] == "user"), None
    )
    question_for_model = (
        f"（上一轮我问的是：{last_question[:120]}）\n本轮问题：{question}"
        if last_question
        else question
    )

    if use_notes:
        from wisnote.ingest import bump_ref_counts
        bump_ref_counts({h.get("document_id") for h in hits})
        sources = build_sources(hits)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history_messages,
            {
                "role": "user",
                "content": f"【笔记片段】\n{build_context(hits)}\n\n【问题】\n"
                           f"{question_for_model}\n\n"
                           f"请基于上述笔记片段完整回答（结构化展开、细节不压缩），"
                           f"引用处标注来源编号及位置（照抄片段头部来源行）。",
            },
        ]
        used_mode = "notes"
    else:
        sources = []
        messages = [
            {"role": "system", "content": FREE_SYSTEM_PROMPT},
            *history_messages,
            {"role": "user", "content": question_for_model},
        ]
        used_mode = "free"

    if result_holder is not None:
        result_holder.update({"sources": sources, "mode": used_mode, "hits": hits, "refused": False})

    full: list[str] = []
    for chunk in _enrich_stream(chat_stream(messages, options={"num_predict": 768}), sources):
        full.append(chunk)
        yield chunk

    if save:
        _save_turn(session_id, question, "".join(full), sources)
        # 第一轮结束后自动给会话命名（便于日后查找）
        if prior_msg_count == 0:
            try:
                auto_name_session(session_id)
            except Exception:
                pass
        # 长对话记忆：消息过多时自动把更早的对话压缩为摘要
        try:
            from wisnote.chat import compress_history
            if len(get_messages(session_id)) > AUTO_COMPRESS_THRESHOLD:
                compress_history(session_id)
        except Exception:
            pass


def format_answer(result: dict, show_sources: bool = True) -> str:
    """把回答渲染成带溯源列表的文本（命令行/界面共用）"""
    lines = [result["answer"]]
    if show_sources and result["sources"]:
        lines.append("")
        lines.append("引用来源：")
        for s in result["sources"]:
            loc = f"第{s['page']}页" if s["page"] else "无页码"
            if s["para_index"] is not None:
                loc += f" 第{s['para_index']}段"
            lines.append(f"  [{s['index']}] {s['title']} | {loc} | 相似度 {s['similarity']}")
    return "\n".join(lines)
