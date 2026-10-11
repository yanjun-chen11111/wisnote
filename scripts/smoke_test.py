"""第1阶段自测脚本：注册 → 上传 → 解析 → 切块 → 向量化 → 检索，一键跑通全链路

运行方式（项目根目录下）：
    python scripts/smoke_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # PyMuPDF

from wisnote import auth
from wisnote.config import ensure_dirs
from wisnote.db import init_db
from wisnote.ingest import delete_document, ingest_document, list_documents
from wisnote.search import search_notes

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TXT_SAMPLE = SAMPLES / "操作系统笔记.txt"
PDF_SAMPLE = SAMPLES / "操作系统笔记.pdf"

TEST_USER = "smoke_tester"
TEST_PASS = "123456"


def make_sample_pdf() -> None:
    """用 PyMuPDF 生成一个两页中文 PDF 样例（含页码，可验证 PDF 溯源）"""
    if PDF_SAMPLE.exists():
        return
    page1 = [
        "第一章 计算机网络概述",
        "计算机网络是若干节点和连接这些节点的链路组成的系统。最著名的网络体系结构是OSI七层模型和TCP/IP四层模型。",
        "OSI参考模型将网络通信划分为物理层、数据链路层、网络层、传输层、会话层、表示层和应用层共七层。",
        "TCP/IP模型则简化为网络接口层、网际层、传输层和应用层四层，是事实上的工业标准。",
    ]
    page2 = [
        "第二章 传输层协议",
        "TCP是面向连接的、可靠的传输层协议，通过三次握手建立连接，四次挥手释放连接。",
        "TCP通过确认重传、滑动窗口和拥塞控制机制保证可靠传输。滑动窗口协议实现了流量控制。",
        "UDP是无连接的传输层协议，开销小、实时性好，适用于视频会议和域名解析等场景。",
    ]
    doc = pymupdf.open()
    for texts in (page1, page2):
        page = doc.new_page()
        y = 72
        for t in texts:
            page.insert_text((72, y), t, fontname="china-s", fontsize=12)
            y += 24
    doc.save(PDF_SAMPLE)
    doc.close()


def main() -> None:
    print("=" * 60)
    print("WisNote 第1阶段自测：数据流水线 + 用户系统")
    print("=" * 60)

    # 0. 初始化
    ensure_dirs()
    init_db()

    # 1. 用户系统
    try:
        uid = auth.register(TEST_USER, TEST_PASS)
        print(f"[1] 注册成功：{TEST_USER} (user_id={uid})")
    except auth.AuthError:
        pass
    user = auth.login(TEST_USER, TEST_PASS)
    assert auth.get_user_by_token(user["token"]) is not None
    print(f"[1] 登录成功，会话 token 有效")
    uid = user["user_id"]

    # 1.5 清理历史测试数据，保证重复运行结果一致
    for d in list_documents(uid):
        delete_document(uid, d["id"])
    print("[1] 已清理历史测试数据")

    # 2. 准备样例文件
    make_sample_pdf()
    assert TXT_SAMPLE.exists()

    # 3. 入库（txt + pdf 两种类型，覆盖无页码/有页码两种溯源）
    for f in (TXT_SAMPLE, PDF_SAMPLE):
        r = ingest_document(uid, f, course_name="操作系统")
        print(f"[2] 入库成功：{r['title']}  段落={r['blocks']} 块={r['chunks']}")

    # 4. 资料列表
    docs = list_documents(uid)
    print(f"[3] 资料列表共 {len(docs)} 条：")
    for d in docs:
        print(f"      - #{d['id']} {d['title']} [{d['file_type']}] 课程={d['course_name']} 状态={d['status']}")

    # 5. 语义检索（第1块的 embedding 模型首次运行会自动下载约100MB，请耐心）
    for q in ("怎么避免死锁？", "TCP如何保证可靠传输", "页面置换算法有哪些"):
        print(f"\n[4] 检索：{q}")
        for h in search_notes(uid, q, top_k=2):
            loc = f"第{h['page']}页" if h["page"] else "无页码"
            print(f"      相似度 {h['similarity']:.4f} | {h['title']} | {loc} 段{h['para_index']}")
            print(f"      {h['text'][:60]}...")

    # 6. 退出登录
    auth.logout(user["token"])
    assert auth.get_user_by_token(user["token"]) is None
    print("\n[5] 退出登录成功，会话已销毁")
    print("\n" + "=" * 60)
    print("全部通过：第1阶段数据流水线运行正常 [PASS]")
    print("=" * 60)


if __name__ == "__main__":
    main()
