"""知识点讲解（计划书模块3）：四种模式，单模型 Prompt 路由

四种模式共用同一个 Qwen3-1.7B，只切换系统提示词与任务描述：
  1. plain    通俗讲解：大白话 + 生活类比，适合零基础
  2. steps    分步讲解：把知识拆成有序步骤/流程，适合操作类知识
  3. socratic 问答引导：先抛问题引导思考，再逐步揭示答案
  4. exam     考点分析：常考点、易错点、典型题型、答题要点

共同约束：只能依据检索到的笔记片段，标注 [1][2] 来源，笔记里没有就明说。
"""

from wisnote.config import SEARCH_TOP_K
from wisnote.llm import chat_stream
from wisnote.rag import build_context, build_sources, max_semantic_similarity
from wisnote.search import search_notes

# 依据笔记讲解时的约束（可溯源）
RULES_GROUNDED = """共同规则：
1. 只能依据【笔记片段】讲解，不得引入笔记之外的知识，不得编造；
2. 用到片段内容处标注来源编号，如 [1]；
3. 笔记信息不足以讲解时，先说明"笔记中相关内容有限"，再基于已有内容讲，不猜；
4. 全程用中文，条理清晰。"""

# 不依据笔记、用模型自身知识讲解时的约束（无溯源）
RULES_FREE = """共同规则：
1. 本次没有可参考的笔记，请基于你自身的知识讲解；
2. 讲解要准确、系统、条理清晰，能分点就分点，必要时举例说明；
3. 对没有把握的内容要说明"这部分我不确定"，不要编造具体数据或结论；
4. 全程用中文。"""

EXPLAIN_MODES: dict[str, dict] = {
    "plain": {
        "label": "通俗讲解",
        "system": "你是善于把复杂概念讲简单的老师，擅长用生活类比解释抽象概念。",
        "task": "请通俗易懂地讲解「{topic}」，做到："
                "\n(1) 先用一句话说清它是什么；"
                "\n(2) 再用一个生活类比帮助理解；"
                "\n(3) 最后给出这个知识点最关键的 2~3 个要点。",
    },
    "steps": {
        "label": "分步讲解",
        "system": "你是善于拆解流程的讲师，讲课时习惯把知识拆成清晰的步骤。",
        "task": "请把「{topic}」拆成有序步骤讲解，做到："
                "\n(1) 先给出整体流程概览（一句话）；"
                "\n(2) 然后按 1、2、3… 逐步展开，每步说明「做什么 + 为什么」；"
                "\n(3) 最后总结各步骤之间的先后依赖关系。",
    },
    "socratic": {
        "label": "问答引导",
        "system": "你是善于启发式教学的老师，习惯用提问引导学生自己想明白。",
        "task": "请用问答引导的方式讲解「{topic}」，做到："
                "\n(1) 先提出 2~3 个引导性问题，引发思考；"
                "\n(2) 逐个问题给出简明解答；"
                "\n(3) 最后用一个小结把答案串起来，并留一个进阶思考题。",
    },
    "exam": {
        "label": "考点分析",
        "system": "你是经验丰富的考研/期末复习辅导老师，熟悉出题规律与常见失分点。",
        "task": "请对「{topic}」做考点分析，包含四部分："
                "\n(1) 常考形式（选择/简答/计算/综合题等）；"
                "\n(2) 核心考点清单（必须记住的结论）；"
                "\n(3) 易错点与易混概念；"
                "\n(4) 答题要点与得分技巧。",
    },
}

NO_MATERIAL = "笔记中关于「{}」的内容不足，无法充分讲解。请先补充相关笔记资料。"


def list_modes() -> list[tuple[str, str]]:
    """返回 [(模式key, 中文名)]"""
    return [(k, v["label"]) for k, v in EXPLAIN_MODES.items()]


def build_messages(
    topic: str, mode: str, hits: list[dict], grounded: bool = True
) -> list[dict]:
    """组装讲解提示词

    grounded=True：依据笔记片段讲解（要求标注来源编号）
    grounded=False：不提供参考材料，用模型自身知识讲解
    """
    if mode not in EXPLAIN_MODES:
        raise ValueError(f"未知讲解模式：{mode}，可选：{list(EXPLAIN_MODES)}")
    cfg = EXPLAIN_MODES[mode]
    rules = RULES_GROUNDED if grounded else RULES_FREE
    if grounded:
        content = (
            f"【笔记片段】\n{build_context(hits)}\n\n【讲解任务】\n"
            f"{cfg['task'].format(topic=topic)}\n\n请开始讲解，并标注来源编号。"
        )
    else:
        content = f"【讲解任务】\n{cfg['task'].format(topic=topic)}\n\n请开始讲解。"
    return [
        {"role": "system", "content": f"{cfg['system']}\n{rules}"},
        {"role": "user", "content": content},
    ]


def stream_explain(
    user_id: int,
    topic: str,
    mode: str = "plain",
    top_k: int = SEARCH_TOP_K,
    session_id: int | None = None,
    save: bool = True,
    min_similarity: float = 0.30,
    result_holder: dict | None = None,
    source: str = "auto",
):
    """流式讲解：逐字输出，结束后自动存档

    source（讲解依据）：
      auto  = 笔记优先：检索到相关资料就结合笔记讲（可溯源）；
              笔记里没有时，改用模型自身知识讲解（不拒答）
      notes = 只用笔记：笔记里没有就明确说明（严格可溯源，不调用模型编造）
      llm   = 只用模型知识：不检索笔记，纯大模型讲解（无溯源）
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("讲解主题不能为空")
    if mode not in EXPLAIN_MODES:
        raise ValueError(f"未知讲解模式：{mode}")
    if source not in ("auto", "notes", "llm"):
        raise ValueError("讲解依据必须是 auto / notes / llm")

    label = EXPLAIN_MODES[mode]["label"]
    hits = [] if source == "llm" else search_notes(user_id, topic, top_k)
    has_material = bool(hits) and max_semantic_similarity(hits) >= min_similarity

    # 严格模式且无资料：不调用模型，直接说明
    if source == "notes" and not has_material:
        if result_holder is not None:
            result_holder.update({"sources": [], "mode_label": label, "grounded": False})
        text = NO_MATERIAL.format(topic)
        if save and session_id:
            from wisnote.chat import add_message
            add_message(session_id, "user", f"[{label}] 请讲解：{topic}")
            add_message(session_id, "assistant", text, sources=[])
        yield text
        return

    use_notes = has_material and source in ("auto", "notes")
    if use_notes:
        from wisnote.ingest import bump_ref_counts
        bump_ref_counts({h.get("document_id") for h in hits})
        sources = build_sources(hits)
        messages = build_messages(topic, mode, hits, grounded=True)
        prefix = ""
    else:
        sources = []
        messages = build_messages(topic, mode, [], grounded=False)
        prefix = "ℹ️ 笔记中未找到相关内容，以下基于大模型通用知识讲解（无溯源）。\n\n" if source == "auto" else ""

    if result_holder is not None:
        result_holder.update({"sources": sources, "mode_label": label, "grounded": use_notes})

    if prefix:
        yield prefix

    full: list[str] = []
    for chunk in chat_stream(messages, options={"num_predict": 1024}):
        full.append(chunk)
        yield chunk

    if save and session_id:
        from wisnote.chat import add_message
        add_message(session_id, "user", f"[{label}] 请讲解：{topic}")
        add_message(session_id, "assistant", prefix + "".join(full), sources=sources)
