"""第1阶段交互式命令行：手动验证注册/上传/检索

运行方式（项目根目录下）：
    python scripts/demo_cli.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wisnote import auth
from wisnote.config import SUPPORTED_EXTS, ensure_dirs
from wisnote.db import init_db
from wisnote.ingest import delete_document, ingest_document, list_documents
from wisnote.search import search_notes

ensure_dirs()
init_db()

user: dict | None = None  # {"user_id":..., "username":..., "token":...}


def require_login() -> int:
    if user is None:
        print("请先登录（菜单 1/2）")
        raise SystemExit(1)
    return user["user_id"]


def do_register() -> None:
    username = input("用户名：").strip()
    password = input("密码：").strip()
    uid = auth.register(username, password)
    print(f"注册成功，user_id={uid}，请登录")


def do_login() -> None:
    global user
    username = input("用户名：").strip()
    password = input("密码：").strip()
    user = auth.login(username, password)
    print(f"欢迎回来，{user['username']}")


def do_upload() -> None:
    uid = require_login()
    path = input("文件路径：").strip().strip('"').strip("'")
    if Path(path).suffix.lower() not in SUPPORTED_EXTS:
        print(f"不支持的类型，当前支持：{'/'.join(SUPPORTED_EXTS)}")
        return
    course = input("课程分类（可留空）：").strip() or None
    try:
        r = ingest_document(uid, path, course_name=course)
        print(f"入库成功：{r['title']}（段落 {r['blocks']} → 块 {r['chunks']}）")
    except Exception as e:
        print(f"入库失败：{e}")


def do_list() -> None:
    uid = require_login()
    docs = list_documents(uid)
    if not docs:
        print("（还没有资料，先上传一个）")
        return
    for d in docs:
        course = d["course_name"] or "-"
        print(f"#{d['id']} [{d['file_type']}] {d['title']}  课程:{course}  状态:{d['status']}  {d['created_at']}")


def do_search() -> None:
    uid = require_login()
    q = input("你的问题：").strip()
    if not q:
        return
    hits = search_notes(uid, q)
    if not hits:
        print("（没有找到相关内容，先上传笔记吧）")
        return
    for i, h in enumerate(hits, 1):
        loc = f"第{h['page']}页" if h["page"] else "无页码"
        print(f"\n{i}. 相似度 {h['similarity']:.4f} | {h['title']} | {loc} 第{h['para_index']}段")
        print(f"   {h['text']}")


def do_delete() -> None:
    uid = require_login()
    doc_id = input("要删除的资料编号 #：").strip()
    try:
        delete_document(uid, int(doc_id))
        print("已删除")
    except Exception as e:
        print(f"删除失败：{e}")


MENU = """
========== WisNote 命令行（第1阶段） ==========
当前用户：{who}
  1. 注册
  2. 登录
  3. 上传资料 (pdf/pptx/docx/txt/md)
  4. 查看我的资料
  5. 语义检索
  6. 删除资料
  0. 退出
============================================="""


def main() -> None:
    actions = {"1": do_register, "2": do_login, "3": do_upload, "4": do_list, "5": do_search, "6": do_delete}
    while True:
        print(MENU.format(who=user["username"] if user else "未登录"))
        choice = input("选择：").strip()
        if choice == "0":
            if user:
                auth.logout(user["token"])
            print("再见！")
            return
        action = actions.get(choice)
        if action:
            try:
                action()
            except auth.AuthError as e:
                print(f"认证失败：{e}")
        else:
            print("无效选择")


if __name__ == "__main__":
    main()
