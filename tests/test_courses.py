"""B. 课程受控词表测试：等价类 + 幂等性 + 用户隔离"""

from wisnote.db import get_conn
from wisnote.ingest import get_or_create_course, list_courses


def _course_count(user_id: int, name: str) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM courses WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def test_create_new_course(user_id):
    """B1 新课程：创建成功且出现在列表中"""
    cid = get_or_create_course(user_id, "操作系统")
    assert cid > 0
    names = [c["name"] for c in list_courses(user_id)]
    assert "操作系统" in names


def test_idempotent_same_name(user_id):
    """B2 幂等：同名创建两次 -> 同一 id，仅一行"""
    c1 = get_or_create_course(user_id, "数学")
    c2 = get_or_create_course(user_id, "数学")
    assert c1 == c2
    assert _course_count(user_id, "数学") == 1


def test_whitespace_stripped(user_id):
    """B3 等价类：首尾空白不影响匹配"""
    c1 = get_or_create_course(user_id, "数学")
    c2 = get_or_create_course(user_id, "  数学  ")
    assert c1 == c2
    assert _course_count(user_id, "数学") == 1


def test_user_isolation(user_id):
    """B4 隔离：不同用户同名课程互不共享"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, 'x')",
            (f"iso_{user_id}",),
        )
        conn.commit()
        other = int(cur.lastrowid)
    finally:
        conn.close()
    c1 = get_or_create_course(user_id, "英语")
    c2 = get_or_create_course(other, "英语")
    assert c1 != c2
    assert _course_count(user_id, "英语") == 1
    assert _course_count(other, "英语") == 1


def test_case_sensitivity_pinned(user_id):
    """B5 行为钉死：SQLite TEXT 默认大小写敏感，"OS" 与 "os" 是两门课（现状，非 bug）"""
    c1 = get_or_create_course(user_id, "OS")
    c2 = get_or_create_course(user_id, "os")
    assert c1 != c2
