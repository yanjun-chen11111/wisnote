"""大模型接口：通过 Ollama HTTP API 调用本地 Qwen3-1.7B

设计要点（对应计划书 3.2 / 3.3 / 3.4）：
- 只加载一个模型：所有文本生成任务（问答、讲解、摘要、社区回答）都走它；
- 任务区分靠 Prompt 路由，不靠换模型；
- keep_alive=-1：模型常驻内存，避免每次调用重新加载（32GB 内存足够）；
- num_thread=16：纯 CPU 推理用满核心。

Ollama 启动前请设置环境变量（一次性）：
    OLLAMA_MAX_LOADED_MODELS=1
    OLLAMA_KEEP_ALIVE=-1
    OLLAMA_NUM_THREADS=16
"""

import json
import re
import urllib.error
import urllib.request

from wisnote.config import LLM_MODEL, LLM_OPTIONS, LLM_TIMEOUT, OLLAMA_HOST


class LLMError(Exception):
    """大模型调用失败（Ollama 未启动、模型不存在、超时等）"""


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    """去掉 Qwen3 可能输出的思维链内容，只保留最终回答"""
    return _THINK_RE.sub("", text or "").strip()


def chat(
    messages: list[dict],
    model: str | None = None,
    options: dict | None = None,
    timeout: int | None = None,
) -> str:
    """调用 Ollama 的 /api/chat，返回模型回答文本

    messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
    """
    payload = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": -1,        # 常驻内存
        "think": False,          # 关闭思考模式，CPU 上更快
    }
    opts = dict(LLM_OPTIONS)
    if options:
        opts.update(options)
    payload["options"] = opts

    req = urllib.request.Request(
        OLLAMA_HOST + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or LLM_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise LLMError(
            f"无法连接 Ollama（{OLLAMA_HOST}）。请确认 Ollama 已启动且模型已下载：{e}"
        ) from e
    except TimeoutError as e:
        raise LLMError(f"模型生成超时（{timeout or LLM_TIMEOUT}秒），CPU 首次推理会较慢") from e

    content = (data.get("message") or {}).get("content", "")
    return strip_think(content)


def _clean_stream(chunks):
    """过滤流式输出中可能混入的思考链内容；正常内容必须逐段即时透传

    修复记录：早期版本在"未出现 think 标记"的常态下把内容攒到流结束
    才一次性 yield（E2E 实测 chunk数=1 暴露），导致所谓流式从未生效。
    现在只保留"标记长度-1"的尾巴防止标记被拆在两个 chunk 交界，
    其余内容到达即透传。

    标记字面量统一从 _THINK_RE 的 pattern 运行时提取，不在代码里重复手写。
    """
    open_tag, _, close_tag = _THINK_RE.pattern.partition(".*?")
    keep = max(len(open_tag), len(close_tag)) - 1

    buf = ""
    in_think = False
    for c in chunks:
        buf += c
        while True:
            if in_think:
                end = buf.find(close_tag)
                if end != -1:
                    buf = buf[end + len(close_tag):]
                    in_think = False
                    continue
                # 思考内容：丢弃，只保留末尾可能是半个闭标记的尾巴
                buf = buf[-keep:] if keep and len(buf) > keep else buf
                break
            start = buf.find(open_tag)
            if start != -1:
                head = buf[:start]
                buf = buf[start + len(open_tag):]
                in_think = True
                if head:
                    yield head
                continue
            # 无标记：立即透传安全前缀，只留防分裂尾巴
            if len(buf) > keep:
                yield buf[:-keep]
                buf = buf[-keep:]
            break

    if not in_think and buf:
        yield buf


def chat_stream(
    messages: list[dict],
    model: str | None = None,
    options: dict | None = None,
    timeout: int | None = None,
):
    """流式调用：逐块产出回答文本（边生成边显示，显著改善等待体验）"""
    payload = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "stream": True,
        "keep_alive": -1,
        "think": False,
    }
    opts = dict(LLM_OPTIONS)
    if options:
        opts.update(options)
    payload["options"] = opts

    req = urllib.request.Request(
        OLLAMA_HOST + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    def raw_iter():
        try:
            with urllib.request.urlopen(req, timeout=timeout or LLM_TIMEOUT) as r:
                for line in r:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if data.get("done"):
                        break
                    c = (data.get("message") or {}).get("content", "")
                    if c:
                        yield c
        except urllib.error.URLError as e:
            raise LLMError(
                f"无法连接 Ollama（{OLLAMA_HOST}）。请确认 Ollama 已启动：{e}"
            ) from e

    return _clean_stream(raw_iter())


def ping() -> bool:
    """检查 Ollama 服务是否可用"""
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def has_model(model: str | None = None) -> bool:
    """检查指定模型是否已下载"""
    name = model or LLM_MODEL
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        return any(m.get("name", "").startswith(name.split(":")[0]) for m in data.get("models", []))
    except Exception:
        return False


def warmup() -> str:
    """预热：触发模型加载（首次调用会慢，之后常驻内存）"""
    return chat(
        [{"role": "user", "content": "回复两个字：你好"}],
        options={"num_predict": 16},
    )
