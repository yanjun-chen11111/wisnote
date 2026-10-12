"""共享夹具：临时数据库与目录隔离，绝不触碰真实 data/

注意：wisnote 各模块在 import 时绑定路径常量（from wisnote.config import DB_PATH），
因此必须在导入任何 wisnote 模块之前重定向 config 的路径属性。
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wisnote.config as _cfg

_TMP = Path(tempfile.mkdtemp(prefix="wisnote_pytest_"))
_cfg.DB_PATH = _TMP / "t.db"
_cfg.UPLOADS_DIR = _TMP / "uploads"
_cfg.CHROMA_DIR = _TMP / "chroma"

from wisnote.db import get_conn, init_db  # noqa: E402

init_db()

import pytest  # noqa: E402

_counter = {"n": 0}


@pytest.fixture()
def user_id() -> int:
    """每个测试一个独立用户（数据互不干扰）"""
    _counter["n"] += 1
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, 'x')",
            (f"user_{_counter['n']}",),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


@pytest.fixture()
def make_text_image(tmp_path: Path):
    """生成含打印体中文文字的 PNG（供真实 OCR 测试）"""

    def _make(name: str, lines: list[tuple[int, str]]) -> Path:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (640, 320), "white")
        draw = ImageDraw.Draw(img)
        font = None
        for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
            try:
                font = ImageFont.truetype(fp, 36)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        for y, text in lines:
            draw.text((40, y), text, fill="black", font=font)
        path = tmp_path / name
        img.save(path)
        return path

    return _make
