"""WisNote 智能笔记学习平台 — Streamlit 可演示界面

界面层只调用 wisnote 核心库，不含业务逻辑，后续换 PySide6 桌面版时可直接复用核心库。

运行方式（项目根目录，激活 venv 后）：
    pip install streamlit
    streamlit run ui/app.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st
import streamlit.components.v1 as components

from wisnote import auth
from wisnote.chat import (
    create_session,
    get_messages,
    list_sessions,
    search_history,
)
from wisnote.chat import (
    backfill_session_titles,
    delete_empty_sessions,
    get_context_summary,
    get_session,
    rename_session,
)
from wisnote.config import HISTORY_TURNS, SESSION_TTL_HOURS, SUPPORTED_EXTS
from wisnote.db import init_db
from wisnote.explain import list_modes, stream_explain
from wisnote.community import VISIBILITIES
from wisnote.ingest import (
    delete_document,
    ingest_document,
    list_courses,
    list_documents,
    update_document,
)
from wisnote.community import (
    add_comment,
    hot_documents,
    import_document,
    list_comments,
    list_favorites,
    list_following,
    list_public_documents,
    list_users,
    search_community,
    share_document,
    toggle_favorite,
    toggle_follow,
    toggle_like,
)
from wisnote.highlight import can_highlight, get_document_info, render_pdf_page
from wisnote.groups import (
    add_group_document,
    ask_group,
    create_group,
    is_member,
    join_group,
    list_group_documents,
    list_groups,
    list_messages,
    post_message,
)
from wisnote.llm import LLMError, has_model, ping
from wisnote.qa import add_answer, ask_question, get_question, list_questions, mark_best_answer
from wisnote.rag import ask, auto_ask, free_ask, stream_ask
from wisnote.summary import get_summary, summarize_document

init_db()
st.set_page_config(page_title="WisNote 智能笔记学习平台", page_icon="📚", layout="wide")

for key in ("user", "session_id", "doc_id"):
    if key not in st.session_state:
        st.session_state[key] = None

# ---------- 登录令牌存储方式（会话 Cookie，浏览器级） ----------
# 令牌存于浏览器会话 Cookie（无过期时间 → 浏览器关闭即失效）：
#   刷新页面 / 关标签页再开（浏览器未关）→ 保持登录
#   关闭浏览器                                 → 失效，需重新登录
# 不出现在地址栏、不写磁盘。
# 实现依据（E2E 实测）：旧自建 sessionStorage 组件在新版 Streamlit 下
# 握手超时（Custom Component timeout error），render 事件从未送达，
# 令牌从未写入浏览器 → 刷新必掉线。现改用官方 API：
#   读：st.context.cookies（随页面请求同步带来，无组件回传时序问题）
#   写/清：st.components.v1.html 注入 JS 操作 document.cookie（无需握手）
TOKEN_KEY = "wisnote_token"


def _write_token(value: str) -> None:
    """把令牌写入会话 Cookie（JS 写入，浏览器关闭自动失效）"""
    try:
        components.html(
            f"<script>document.cookie='{TOKEN_KEY}={value}; path=/; SameSite=Lax';</script>",
            height=0,
        )
    except Exception:
        pass


def _clear_token() -> None:
    """清除令牌 Cookie（置为过去过期时间）"""
    try:
        components.html(
            f"<script>document.cookie='{TOKEN_KEY}=; path=/; Max-Age=0; SameSite=Lax';</script>",
            height=0,
        )
    except Exception:
        pass


def _read_token() -> str | None:
    """从本次请求的 Cookie 中同步读取令牌（官方 API，无时序问题）"""
    try:
        v = st.context.cookies.get(TOKEN_KEY)
        return v or None
    except Exception:
        return None


def persist_login(token: str) -> None:
    """登录成功：令牌写入会话 Cookie，并清理遗留的地址栏参数"""
    _write_token(token)
    try:
        st.query_params.clear()
    except Exception:
        pass


def clear_login() -> None:
    """退出登录：清除令牌 Cookie 与网址参数"""
    _clear_token()
    try:
        st.query_params.clear()
    except Exception:
        pass


def try_restore_session() -> None:
    """刷新后恢复登录：从请求 Cookie 同步取令牌（无需等待组件回传）"""
    if st.session_state.user is not None:
        return
    token = _read_token()
    if token:
        try:
            u = auth.get_user_by_token(token)
        except Exception:
            u = None
        if u:
            st.session_state.user = {
                "token": token,
                "user_id": u["user_id"],
                "username": u["username"],
            }
        else:
            # Cookie 里的令牌已过期/吊销：清掉避免每次请求都校验失败
            _clear_token()


try_restore_session()


# ---------------- 登录 / 注册 ----------------
def page_login() -> None:
    st.title("WisNote 智能笔记学习平台")
    st.caption("基于 RAG 的个人笔记助手：检索 · AI 讲解 · 原文溯源 · 聊天记录")
    tab_login, tab_reg = st.tabs(["登录", "注册"])
    with tab_login:
        u = st.text_input("用户名", key="lg_u")
        p = st.text_input("密码", type="password", key="lg_p")
        st.caption("登录后刷新页面不会掉线；关闭浏览器需重新登录")
        if st.button("登录", type="primary"):
            try:
                st.session_state.user = auth.login(u, p)
                st.session_state.session_id = None
                persist_login(st.session_state.user["token"])
                st.success(f"欢迎，{u}")
                st.rerun()
            except auth.AuthError as e:
                st.error(str(e))
    with tab_reg:
        u2 = st.text_input("新用户名", key="rg_u")
        p2 = st.text_input("新密码", type="password", key="rg_p")
        if st.button("注册"):
            try:
                auth.register(u2, p2)
                st.success("注册成功，请登录")
            except auth.AuthError as e:
                st.error(str(e))


def require_user() -> int:
    return st.session_state.user["user_id"]


# ---------------- 资料管理 ----------------
def page_docs() -> None:
    st.header("📁 资料管理")
    uid = require_user()

    with st.expander("上传新资料", expanded=False):
        up = st.file_uploader(
            "选择文件（PDF / PPT / Word / 文本 / 图片）",
            type=[e.lstrip(".") for e in SUPPORTED_EXTS],
        )
        _courses = [c["name"] for c in list_courses(uid)]
        _new = "＋ 新建课程…"
        _sel = st.selectbox("课程分类", [_new] + _courses, index=0)
        course = st.text_input("新课程名称", placeholder="如：操作系统") if _sel == _new else _sel
        _is_img = bool(up) and up.name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))
        auto_sum = st.checkbox("上传后自动生成摘要与标签", value=True)
        if _is_img:
            st.caption("图片会先 OCR 识别文字：识别到即参与 AI 检索与摘要；识别不到则仅存档预览")
        if st.button("开始入库") and up is not None:
            suffix = Path(up.name).suffix.lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(up.getbuffer())
                tmp_path = tmp.name
            try:
                r = ingest_document(uid, tmp_path, course_name=course or None, title=Path(up.name).stem)
                if r["chunks"] == 0:
                    st.warning(f"入库成功：{r['title']}（图片未识别出文字，仅存档与预览，不参与 AI 检索）")
                else:
                    st.success(f"入库成功：{r['title']}（段落 {r['blocks']} → 块 {r['chunks']}）")
                if auto_sum and r["chunks"] > 0:
                    with st.spinner("AI 正在生成摘要与标签（CPU 约 20~40 秒）..."):
                        summarize_document(uid, r["document_id"])
                    st.success("摘要与标签已生成")
                st.rerun()
            except Exception as e:
                st.error(f"入库失败：{e}")

    courses = list_courses(uid)
    course_names = [c["name"] for c in courses]
    pick = st.selectbox("按课程筛选", ["（全部）"] + course_names)
    docs = list_documents(uid, None if pick == "（全部）" else pick)

    if not docs:
        st.info("还没有资料，先上传一份吧")
        return

    for d in docs:
        with st.container(border=True):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.subheader(f"#{d['id']} {d['title']}")
                _type = "图片" if d["file_type"] in ("jpg", "jpeg", "png", "gif", "bmp", "webp") else d["file_type"]
                st.caption(
                    f"类型 {_type} | 课程 {d['course_name'] or '-'} | "
                    f"被引用 {d['ref_count']} 次 | 上传于 {d['created_at']}"
                )
                if d["file_type"] in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
                    try:
                        st.image(d["file_path"], caption=d["title"], use_column_width=True)
                    except Exception:
                        pass
            with col2:
                vis = st.selectbox(
                    "可见性",
                    VISIBILITIES,
                    index=VISIBILITIES.index(d.get("visibility") or "private"),
                    key=f"vis_{d['id']}",
                    label_visibility="collapsed",
                )
                if vis != (d.get("visibility") or "private"):
                    try:
                        share_document(uid, d["id"], vis)
                        st.success(f"已设为：{vis}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                if st.button("删除", key=f"del_{d['id']}"):
                    delete_document(uid, d["id"])
                    st.rerun()

            with st.expander("更新 / 替换该资料"):
                new_file = st.file_uploader(
                    "选择新版本文件", key=f"upd_{d['id']}", label_visibility="collapsed"
                )
                if st.button("确认替换", key=f"updbtn_{d['id']}") and new_file is not None:
                    suffix = Path(new_file.name).suffix.lower()
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(new_file.getbuffer())
                        tmp_path = tmp.name
                    try:
                        r = update_document(uid, d["id"], tmp_path)
                        st.success(f"已更新：{r['title']}（块 {r['chunks']}）")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新失败：{e}")

            if d["file_type"] in ("jpg", "jpeg", "png", "gif", "bmp", "webp") and not d.get("chunk_count"):
                st.caption("🖼️ 图片资料：未识别出文字，仅存档与预览，不参与 AI 检索 / 摘要")
            else:
                s = get_summary(uid, d["id"])
                if s["summary"]:
                    st.write(f"**AI 摘要**：{s['summary']}")
                    if s["tags"]:
                        st.write("**知识点标签**：" + " · ".join(f"`{t}`" for t in s["tags"]))
                elif st.button("生成摘要与标签", key=f"sum_{d['id']}"):
                    with st.spinner("AI 正在生成（CPU 约 20~40 秒）..."):
                        try:
                            summarize_document(uid, d["id"])
                            st.rerun()
                        except LLMError as e:
                            st.error(f"生成失败：{e}")


# ---------------- 智能问答 ----------------
def render_sources(sources: list[dict]) -> None:
    """展示引用来源；PDF 来源可展开查看原文高亮截图"""
    if not sources:
        return
    with st.expander(f"引用来源（{len(sources)} 条）"):
        for s in sources:
            loc = f"第{s['page']}页" if s["page"] else "无页码"
            if s["para_index"] is not None:
                loc += f" 第{s['para_index']}段"
            st.write(f"**[{s['index']}] {s['title']}** | {loc} | 相似度 {s['similarity']}")
            st.caption(s["snippet"])

            if s.get("page") and s.get("document_id"):
                with st.expander("🖼️ 查看原文高亮", expanded=False):
                    try:
                        info = get_document_info(s["document_id"])
                        _ft = (info or {}).get("file_type", "")
                        if _ft in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
                            st.image(info["file_path"], caption="来源图片（回答引用的文字提取自该图）", use_column_width=True)
                            continue
                        if not info or not can_highlight(_ft):
                            st.info("仅 PDF 支持原文高亮预览，该来源为文本/Word 资料")
                            continue
                        col_a, col_b = st.columns(2)
                        with col_a:
                            full = render_pdf_page(info["file_path"], s["page"], s.get("snippet", ""))
                            st.image(full["image"], caption=f"第{s['page']}页（整页）", use_column_width=True)
                        with col_b:
                            zoom = render_pdf_page(
                                info["file_path"], s["page"], s.get("snippet", ""), zoom=True
                            )
                            if zoom["zoomed"]:
                                st.image(zoom["image"], caption="命中区域放大", use_column_width=True)
                            else:
                                st.caption("该页未定位到精确文字，已展示整页")
                        if full["matched"]:
                            st.success(f"已高亮命中文字：{full['matched'][:50]}…")
                        else:
                            st.warning("未能在该页精确定位文字，可能片段跨页或为扫描件（无文本层）")
                    except Exception as e:
                        st.error(f"预览失败：{e}")


def page_chat() -> None:
    st.header("💬 智能问答")
    uid = require_user()

    mode = st.radio(
        "回答模式",
        ["📚 笔记问答（只依据资料，可溯源）", "🔀 智能模式（有资料用资料，没有自由回答）", "💬 自由对话（通用助手，不依赖资料）"],
        horizontal=True,
        help="笔记问答是严格模式：资料里没有会明确拒答，用于保证回答可信、可溯源；自由对话不检索资料库。",
    )
    mode_key = "notes" if mode.startswith("📚") else "auto" if mode.startswith("🔀") else "free"
    use_history = st.checkbox(
        "带入对话上下文（关闭 = 纯单轮：只用当前问题检索，完全不参考历史）",
        value=(mode_key != "notes"),
        key=f"usehist_{mode_key}",
    )
    if st.session_state.session_id:
        n_msg = len(get_messages(st.session_state.session_id))
        has_sum = bool(get_context_summary(st.session_state.session_id))
        sess = get_session(st.session_state.session_id) or {}
        st.caption(
            f"📌 当前会话：{sess.get('title', '新会话')}（共 {n_msg} 条消息；"
            + (f"本轮带入最近 {HISTORY_TURNS} 条上下文" if use_history else "本轮为单轮独立检索，不带历史")
            + ("；更长对话已生成摘要记忆 ✅" if has_sum else "")
            + "）"
        )
        with st.expander("重命名会话"):
            new_title = st.text_input("会话名称", value=sess.get("title", ""), key="rename_input")
            if st.button("保存名称") and new_title.strip():
                rename_session(st.session_state.session_id, new_title.strip())
                st.rerun()

    # 首次进入：清理空会话 + 给早期会话补标题（只做一次）
    if not st.session_state.get("_tidied"):
        delete_empty_sessions(uid)
        backfill_session_titles(uid)
        st.session_state["_tidied"] = True

    # 懒创建：只有真正发言时才建会话，避免刷新页面产生空会话
    sid = st.session_state.session_id

    col_new, col_hist = st.columns([1, 3])
    with col_new:
        if st.button("新建会话"):
            st.session_state.session_id = None
            st.rerun()
    with col_hist:
        recent = list_sessions(uid)[:5]
        if recent:
            opts = {f"{s['title']}（{s['msg_count']}条）": s["id"] for s in recent}
            pick = st.selectbox("或切换到已有会话", ["（当前会话）"] + list(opts), label_visibility="collapsed")
            if pick != "（当前会话）" and opts[pick] != sid:
                st.session_state.session_id = opts[pick]
                st.rerun()

    if sid:
        for m in get_messages(sid):
            with st.chat_message("user" if m["role"] == "user" else "assistant"):
                st.write(m["content"])
                if m.get("sources"):
                    render_sources(m["sources"])

    q = st.chat_input("基于你的笔记提问…")
    if q:
        if sid is None:
            sid = create_session(uid, "新会话")
            st.session_state.session_id = sid
        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant"):
            holder: dict = {}
            try:
                st.write_stream(
                    stream_ask(
                        uid,
                        q,
                        mode=mode_key,
                        session_id=sid,
                        result_holder=holder,
                        use_history=use_history,
                    )
                )
            except LLMError as e:
                st.error(f"模型调用失败：{e}")
                return
            if holder.get("mode") == "free" and mode_key == "auto":
                st.caption("ℹ️ 笔记中未找到相关内容，已转为通用回答（无溯源）")
            render_sources(holder.get("sources", []))


# ---------------- 知识讲解 ----------------
def page_explain() -> None:
    st.header("🎓 AI 知识点讲解")
    uid = require_user()
    topic = st.text_input("要讲解的知识点", placeholder="如：页面置换算法")
    modes = list_modes()
    mode = st.radio("讲解模式", modes, format_func=lambda x: x[1], horizontal=True)
    source = st.radio(
        "讲解依据",
        ["auto", "notes", "llm"],
        format_func=lambda x: {
            "auto": "笔记优先（有笔记结合笔记讲+可溯源，没有则用模型知识讲）",
            "notes": "只用笔记（笔记里没有就说明，最严谨）",
            "llm": "只用模型知识（不查笔记，任何主题都能讲）",
        }[x],
        help="讲解应由大模型生成；笔记只作为参考资料，不是必须命中才能讲解。",
    )
    if st.button("开始讲解", type="primary") and topic.strip():
        st.subheader(f"{mode[1]}：{topic.strip()}")
        st.caption("⏳ 正在检索笔记并生成讲解，纯 CPU 首次约需 60~120 秒，下面会逐字出现，请勿重复点击")
        holder: dict = {}
        try:
            st.write_stream(
                stream_explain(
                    uid,
                    topic.strip(),
                    mode=mode[0],
                    session_id=st.session_state.session_id or create_session(uid, "网页会话"),
                    result_holder=holder,
                    source=source,
                )
            )
        except LLMError as e:
            st.error(f"模型调用失败：{e}")
            return
        render_sources(holder.get("sources", []))


# ---------------- 聊天记录 ----------------
def page_history() -> None:
    st.header("🗂️ 聊天记录")
    uid = require_user()
    kw = st.text_input("搜索历史问答（关键词）")
    if kw.strip():
        rows = search_history(uid, kw.strip())
        if not rows:
            st.info("没有匹配记录")
        for r in rows:
            who = "你" if r["role"] == "user" else "AI"
            st.write(f"**会话#{r['session_id']} [{who}]**（{r['created_at']}）")
            st.caption(r["content"][:200])
        return

    sessions = list_sessions(uid)
    if not sessions:
        st.info("还没有会话")
        return
    for s in sessions:
        with st.container(border=True):
            st.write(f"**#{s['id']} {s['title']}** — {s['msg_count']} 条消息，更新于 {s['updated_at']}")
            if st.button("▶ 继续这个会话", key=f"open_{s['id']}"):
                st.session_state.session_id = s["id"]
                st.session_state.nav = "智能问答"
                st.rerun()
            with st.expander("展开查看内容"):
                for m in get_messages(s["id"]):
                    who = "你" if m["role"] == "user" else "AI"
                    st.write(f"[{who}] {m['content'][:300]}")


# ---------------- 溯源预览 ----------------
def page_trace() -> None:
    st.header("🔍 溯源预览")
    st.caption("输入页码与文字片段，直接在 PDF 原文中定位并高亮（验证 AI 回答的每一句话）")
    uid = require_user()
    pdf_docs = [d for d in list_documents(uid) if d["file_type"] == "pdf"]
    if not pdf_docs:
        st.info("还没有 PDF 资料，先上传一份 PDF 笔记")
        return

    dmap = {f"#{d['id']} {d['title']}": d["id"] for d in pdf_docs}
    pick = st.selectbox("选择资料", list(dmap))
    doc_id = dmap[pick]
    info = get_document_info(doc_id)

    col1, col2 = st.columns([1, 1])
    with col1:
        page_no = st.number_input("页码", min_value=1, value=1, step=1)
    with col2:
        do_zoom = st.checkbox("显示命中区域放大图", value=True)
    snippet = st.text_area("要定位的文字片段（可留空，则只显示整页）", height=80)

    if st.button("预览", type="primary"):
        try:
            full = render_pdf_page(info["file_path"], int(page_no), snippet)
            st.image(full["image"], caption=f"第{page_no}页（整页）", use_column_width=True)
            if do_zoom and snippet.strip():
                zoom = render_pdf_page(info["file_path"], int(page_no), snippet, zoom=True)
                if zoom["zoomed"]:
                    st.image(zoom["image"], caption="命中区域放大", use_column_width=True)
            if snippet.strip():
                if full["matched"]:
                    st.success(f"已定位并高亮：{full['matched'][:60]}…")
                else:
                    st.warning("该页未找到匹配文字：可能是扫描版 PDF（无文本层）或片段不在这一页")
        except Exception as e:
            st.error(f"预览失败：{e}")


# ---------------- 社区广场 ----------------
def page_community() -> None:
    st.header("🌐 社区广场")
    st.caption("本地多用户模拟社区：公开笔记可被检索、点赞、收藏、评论与一键导入")
    uid = require_user()
    tab1, tab2, tab3, tab4 = st.tabs(["公开笔记", "社区检索", "我的收藏", "关注用户"])

    with tab1:
        hot = hot_documents(5)
        if hot:
            st.write("🔥 热门排行：" + " | ".join(f"{d['title']}（{d['like_count']}赞）" for d in hot))
        docs = list_public_documents()
        if not docs:
            st.info("社区还没有公开笔记，去「资料管理」把某份笔记设为公开吧")
        for d in docs:
            with st.container(border=True):
                st.subheader(f"#{d['id']} {d['title']}")
                st.caption(f"作者 @{d['owner']} | {d['file_type']} | 点赞 {d['like_count']} | 评论 {d['comment_count']}")
                if d.get("summary"):
                    st.write(d["summary"])
                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("👍 点赞/取消", key=f"like_{d['id']}"):
                        toggle_like(uid, d["id"])
                        st.rerun()
                with c2:
                    if st.button("⭐ 收藏", key=f"fav_{d['id']}"):
                        toggle_favorite(uid, d["id"])
                        st.rerun()
                with c3:
                    if st.button("📥 导入到我的库", key=f"imp_{d['id']}"):
                        try:
                            r = import_document(uid, d["id"])
                            st.success(f"已导入：{r['title']}（来源 @{r['original_author']}）")
                        except Exception as e:
                            st.error(str(e))
                with st.expander("查看/发表评论"):
                    for c in list_comments(d["id"]):
                        st.caption(f"@{c['username']}（{c['created_at']}）：{c['content']}")
                    txt = st.text_input("写下你的评论", key=f"cmt_{d['id']}")
                    if st.button("发表", key=f"cmtbtn_{d['id']}") and txt.strip():
                        add_comment(uid, d["id"], txt.strip())
                        st.rerun()

    with tab2:
        q = st.text_input("在社区公开笔记中检索")
        if st.button("检索", key="csearch") and q.strip():
            hits = search_community(q.strip())
            if not hits:
                st.info("没有找到相关内容")
            for h in hits:
                loc = f"第{h['page']}页" if h["page"] else "无页码"
                st.write(f"**{h['title']}**（作者ID {h['owner_id']}）| {loc} | 相似度 {h['similarity']:.3f}")
                st.caption(h["text"][:200])

    with tab3:
        favs = list_favorites(uid)
        if not favs:
            st.info("还没有收藏")
        for f in favs:
            st.write(f"**{f['title']}**（@{f['owner']}）— 收藏于 {f['created_at']}")

    with tab4:
        st.write("关注其他同学，形成学习圈子")
        for u in list_users(exclude_user_id=uid):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"@{u['username']}（ID {u['id']}，注册于 {u['created_at']}）")
            with col2:
                if st.button("关注/取关", key=f"fol_{u['id']}"):
                    toggle_follow(uid, u["id"])
                    st.rerun()
        st.divider()
        st.write("**我关注的**：" + ("、".join(f"@{f['username']}" for f in list_following(uid)) or "暂无"))


# ---------------- 社区问答 ----------------
def page_qa() -> None:
    st.header("❓ 社区问答")
    st.caption("提问后由 AI 基于社区笔记给出初步回答，其他同学可补充纠正，提问者标记最佳答案")
    uid = require_user()

    with st.expander("我要提问", expanded=False):
        title = st.text_input("问题标题", key="q_title")
        detail = st.text_area("补充说明（可选）", key="q_detail")
        if st.button("提交问题（AI 会先尝试回答）") and title.strip():
            with st.spinner("检索社区笔记并生成初步回答…"):
                r = ask_question(uid, title.strip(), detail)
            st.success(f"已发布（问题 #{r['question_id']}）")
            st.write("**AI 初步回答**：")
            st.write(r["ai_answer"])
            render_sources(r["sources"])

    qs = list_questions()
    if not qs:
        st.info("还没有问题")
        return
    options = {f"#{q['id']} {q['title']}（{q['answer_count']} 回答）": q["id"] for q in qs}
    pick = st.selectbox("选择一个问题", list(options))
    if not pick:
        return
    detail = get_question(options[pick])
    st.subheader(detail["title"])
    st.caption(f"提问者 @{detail['asker']} | {detail['created_at']}")
    if detail.get("content"):
        st.write(detail["content"])

    for a in detail["answers"]:
        with st.container(border=True):
            tag = "🤖 AI 初步回答" if a["is_ai"] else f"👤 @{a['author']}"
            if detail.get("best_answer_id") == a["id"]:
                tag += "  ✅ 最佳答案"
            st.write(f"**{tag}**")
            st.write(a["content"])
            if a.get("sources"):
                render_sources(a["sources"])
            if not a["is_ai"] and detail["user_id"] == uid and detail.get("best_answer_id") != a["id"]:
                if st.button("标记为最佳答案", key=f"best_{a['id']}"):
                    mark_best_answer(uid, detail["id"], a["id"])
                    st.rerun()

    st.divider()
    my_answer = st.text_area("补充或纠正这个回答", key="q_answer")
    if st.button("提交我的回答") and my_answer.strip():
        add_answer(uid, detail["id"], my_answer.strip())
        st.success("已提交")
        st.rerun()


# ---------------- 学习小组 ----------------
def page_groups() -> None:
    st.header("👥 学习小组")
    st.caption("按课程/考试创建小组，组内共享资料库，支持组内 RAG 检索与讨论区")
    uid = require_user()

    with st.expander("创建新小组", expanded=False):
        name = st.text_input("小组名称", key="g_name")
        course = st.text_input("课程/主题", key="g_course")
        desc = st.text_area("简介", key="g_desc")
        if st.button("创建") and name.strip():
            gid = create_group(uid, name.strip(), desc, course)
            st.success(f"小组已创建（#{gid}）")
            st.rerun()

    with st.expander("加入其他小组", expanded=False):
        others = [g for g in list_groups() if not is_member(uid, g["id"])]
        if not others:
            st.info("没有可加入的小组")
        for g in others:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"**#{g['id']} {g['name']}**（{g['course'] or '无主题'}）{g['member_count']} 人")
            with col2:
                if st.button("加入", key=f"join_{g['id']}"):
                    join_group(uid, g["id"])
                    st.rerun()

    my_groups = list_groups(uid)
    if not my_groups:
        st.info("你还没有加入任何小组")
        return
    gmap = {f"#{g['id']} {g['name']}": g["id"] for g in my_groups}
    pick = st.selectbox("我的小组", list(gmap))
    if not pick:
        return
    gid = gmap[pick]

    tab_lib, tab_ask, tab_chat = st.tabs(["组内资料", "组内问答", "讨论区"])

    with tab_lib:
        my_docs = list_documents(uid)
        dmap = {f"#{d['id']} {d['title']}": d["id"] for d in my_docs}
        if dmap:
            add_pick = st.selectbox("把我的资料加入小组库", list(dmap))
            if st.button("加入小组库"):
                add_group_document(uid, gid, dmap[add_pick])
                st.success("已加入")
                st.rerun()
        gdocs = list_group_documents(gid)
        if not gdocs:
            st.info("小组资料库还是空的")
        for d in gdocs:
            st.write(f"**{d['title']}**（@{d['owner']}，加入于 {d['added_at']}）")

    with tab_ask:
        q = st.text_input("基于小组资料提问", key="gq")
        if st.button("组内检索并回答", key="gask") and q.strip():
            with st.spinner("检索小组资料并生成回答…"):
                r = ask_group(uid, gid, q.strip())
            st.write(r["answer"])
            render_sources(r["sources"])

    with tab_chat:
        for m in list_messages(gid):
            st.caption(f"@{m['username']}（{m['created_at']}）：{m['content']}")
        msg = st.text_input("发言", key="gmsg")
        if st.button("发送") and msg.strip():
            post_message(uid, gid, msg.strip())
            st.rerun()


# ---------------- 主页装配 ----------------
def main() -> None:
    if st.session_state.user is None:
        page_login()
        return

    with st.sidebar:
        st.title(f"👤 {st.session_state.user['username']}")
        pages = [
            "资料管理", "智能问答", "知识讲解", "溯源预览",
            "聊天记录", "社区广场", "社区问答", "学习小组",
        ]
        if "nav" not in st.session_state:
            st.session_state.nav = "智能问答"
        page = st.radio("功能导航", pages, index=pages.index(st.session_state.nav))
        st.session_state.nav = page
        st.divider()
        if ping() and has_model():
            st.success("Ollama + Qwen3-1.7B 就绪")
        else:
            st.error("Ollama 未就绪：请先 `ollama serve` 并确保 `qwen3:1.7b` 已下载")
        with st.expander("🔒 账号安全"):
            if _read_token() == st.session_state.user.get("token"):
                st.caption("令牌存储：**会话 Cookie（浏览器级）**（刷新保持、关浏览器需重登，地址栏干净 ✅）")
            else:
                st.caption("令牌存储：**暂未读到**（请刷新或重新登录）")
            sessions = auth.list_active_sessions(st.session_state.user["user_id"])
            st.caption(f"当前有效令牌 {len(sessions)} 个，有效期 {SESSION_TTL_HOURS} 小时")
            for s in sessions:
                st.code(f"{s['token'][:16]}...（创建于 {s['created_at']}）", language=None)
            if st.button("吊销全部令牌（下线所有登录）"):
                auth.revoke_all_sessions(st.session_state.user["user_id"])
                clear_login()
                st.session_state.user = None
                st.session_state.session_id = None
                st.rerun()
        if st.button("退出登录"):
            if st.session_state.user:
                try:
                    auth.logout(st.session_state.user["token"])
                except Exception:
                    pass
            clear_login()
            st.session_state.user = None
            st.session_state.session_id = None
            st.rerun()

    {
        "资料管理": page_docs,
        "智能问答": page_chat,
        "知识讲解": page_explain,
        "溯源预览": page_trace,
        "聊天记录": page_history,
        "社区广场": page_community,
        "社区问答": page_qa,
        "学习小组": page_groups,
    }[page]()


if __name__ == "__main__":
    main()
