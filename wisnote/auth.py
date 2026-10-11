"""用户系统：注册 / 登录 / 会话管理

密码使用 bcrypt 哈希存储；会话使用随机 token（桌面单机应用，
token 保存在本地即可，后续 PySide6 界面直接内存持有）。
"""

import secrets
import sqlite3

import bcrypt

from wisnote.config import SESSION_TTL_HOURS
from wisnote.db import get_conn


class AuthError(Exception):
    """认证相关业务错误（用户名重复、密码错误等）"""


def register(username: str, password: str) -> int:
    """注册新用户，返回 user_id"""
    username = username.strip()
    if not (3 <= len(username) <= 32):
        raise AuthError("用户名长度需在 3~32 个字符之间")
    if len(password) < 6:
        raise AuthError("密码至少需要 6 位")
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, pw_hash),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        raise AuthError(f"用户名「{username}」已被注册") from None
    finally:
        conn.close()


def login(username: str, password: str) -> dict:
    """登录，返回 {token, user_id, username}；失败抛 AuthError"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        raise AuthError("用户名或密码错误")

    token = secrets.token_hex(32)
    conn = get_conn()
    try:
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, row["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"token": token, "user_id": row["id"], "username": row["username"]}


def purge_expired_sessions() -> int:
    """清理已过期的登录令牌"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM sessions WHERE datetime(created_at, ?) <= datetime('now', 'localtime')",
            (f"+{SESSION_TTL_HOURS} hours",),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def get_user_by_token(token: str) -> dict | None:
    """根据 token 获取当前登录用户；不存在或已过期返回 None"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND datetime(s.created_at, ?) > datetime('now', 'localtime')",
            (token, f"+{SESSION_TTL_HOURS} hours"),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"user_id": row["id"], "username": row["username"]}


def revoke_all_sessions(user_id: int) -> int:
    """吊销该用户的全部登录令牌（下线所有设备），用于令牌疑似泄露时止损"""
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def list_active_sessions(user_id: int) -> list[dict]:
    """查看该用户当前有效的登录令牌（创建时间）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT token, created_at FROM sessions WHERE user_id = ? "
            "AND datetime(created_at, ?) > datetime('now', 'localtime') ORDER BY created_at DESC",
            (user_id, f"+{SESSION_TTL_HOURS} hours"),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def logout(token: str) -> None:
    """退出登录，销毁会话"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()
