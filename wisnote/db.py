"""SQLite 数据库连接与表结构管理

表结构一览（第1阶段全部建好，后续阶段直接使用）：
- users           用户
- sessions        登录会话（token）
- courses         课程分类（按用户隔离）
- documents       上传的笔记/资料（原文件路径 + 元数据）
- chunks          文本切块（含页码/段落位置元数据，支撑溯源）
- chat_sessions   聊天会话（第4阶段 AI 问答用，先建好）
- chat_messages   聊天消息（含引用来源 sources，JSON 字符串）
"""

import sqlite3

from wisnote.config import DB_PATH, ensure_dirs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token     TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS courses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    course_id  INTEGER,
    title      TEXT NOT NULL,
    filename   TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    file_type  TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ready',
    summary    TEXT,                    -- AI 自动生成的摘要（≤200字）
    tags       TEXT,                    -- AI 提取的知识点标签（顿号分隔）
    ref_count  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id)  REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    page        INTEGER,          -- 页码（1 起）；docx/txt 无页码概念时为 NULL
    para_index  INTEGER,          -- 段落序号（文档内从 1 递增）
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    title           TEXT NOT NULL DEFAULT '新会话',
    context_summary TEXT,            -- 更早对话的压缩摘要（长对话记忆）
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ========== 模块4：社区（本地多用户模拟环境） ==========
-- 笔记分享：资料可见性（private 私有 / course 课程可见 / public 公开）
CREATE TABLE IF NOT EXISTS likes (
    document_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (document_id, user_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    document_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (document_id, user_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS follows (
    follower_id INTEGER NOT NULL,
    followee_id INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (follower_id, followee_id)
);

-- 社区问答：AI 初步回答 + 人工补充，提问者可标记最佳答案
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    best_answer_id INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    user_id     INTEGER,                 -- AI 回答时为 NULL
    content     TEXT NOT NULL,
    is_ai       INTEGER NOT NULL DEFAULT 0,
    sources     TEXT,                    -- AI 回答的引用来源（JSON）
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

-- 学习小组：组内共享资料库 + 讨论区
CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    course      TEXT NOT NULL DEFAULT '',
    owner_id    INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    role      TEXT NOT NULL DEFAULT 'member',
    joined_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS group_documents (
    group_id    INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    added_by    INTEGER NOT NULL,
    added_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (group_id, document_id)
);

CREATE TABLE IF NOT EXISTS group_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    sources    TEXT,              -- AI 回答引用的来源（JSON：文档/页码/段落）
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_user   ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc       ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chat_msgs_session ON chat_messages(session_id);
"""


def get_conn() -> sqlite3.Connection:
    """获取数据库连接（每次操作独立连接，桌面单机场景足够）"""
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_new_columns(conn: sqlite3.Connection) -> None:
    """对已有数据库做轻量迁移（新增列不会自动出现在旧表中）"""
    s_cols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    if "context_summary" not in s_cols:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN context_summary TEXT")

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    for col, ddl in (
        ("summary", "TEXT"),
        ("tags", "TEXT"),
        ("visibility", "TEXT NOT NULL DEFAULT 'private'"),
        ("shared_at", "TEXT"),
        ("original_author", "TEXT"),
        ("origin_doc_id", "INTEGER"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {ddl}")


def init_db() -> None:
    """初始化所有表（幂等，可重复调用）"""
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        _ensure_new_columns(conn)
        conn.commit()
    finally:
        conn.close()
