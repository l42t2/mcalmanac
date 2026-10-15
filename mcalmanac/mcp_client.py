"""零依赖的麦当劳 MCP 客户端（Streamable HTTP）。

用法：
    from mcalmanac.mcp_client import MCDClient
    client = MCDClient()                      # Token 读自环境变量
    result = client.call_tool("available-coupons")
    print(mcalmanac.mcp_client.tool_text(result))

设计要点（对齐 mcp.mcd.cn 的实际行为）：
- 直接 POST https://mcp.mcd.cn/ 调用 tools/call 即可，无需 initialize 握手；
  若服务端要求会话，会自动补做 initialize / notifications/initialized 再重试。
- 响应可能是 application/json，也可能是 text/event-stream（SSE），两者都解析。
- 401 / 429 映射为友好异常，便于 CLI 提示排查。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import __version__

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-03-26"
TOKEN_ENV_NAMES = ("MCD_MCP_TOKEN", "MCD_TOKEN")


class MCPError(Exception):
    """MCP 调用失败的通用异常。"""


class MCPAuthError(MCPError):
    """401：Token 缺失、无效或过期。"""


class MCPRateLimitError(MCPError):
    """429：超过 600 次/分钟限流。"""


class MCPNetworkError(MCPError):
    """网络不可达、超时等。"""


def _parse_sse(body: str) -> list:
    """解析 SSE 文本，返回其中所有 data: 行组成的 JSON 消息列表。"""
    messages = []
    data_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    messages.append(json.loads(payload))
                except json.JSONDecodeError:
                    continue
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        try:
            messages.append(json.loads("\n".join(data_lines)))
        except json.JSONDecodeError:
            pass
    return messages


def tool_text(result) -> str:
    """从 tools/call 的 result 中取出文本内容（兼容 content / structuredContent）。"""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    texts = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    texts.append(item["text"])
    if texts:
        return "\n".join(texts)
    if "structuredContent" in result:
        return json.dumps(result["structuredContent"], ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def tool_json(result):
    """尝试把 tools/call 的文本内容解析为 JSON；失败则返回原文。"""
    text = tool_text(result)
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return text


class MCDClient:
    """麦当劳 MCP 的极简客户端。"""

    def __init__(self, token: str | None = None, url: str | None = None, timeout: float = 20.0):
        self.token = token or next(
            (os.environ.get(name, "").strip() for name in TOKEN_ENV_NAMES if os.environ.get(name)), ""
        )
        self.url = (url or os.environ.get("MCD_MCP_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self._id = 0
        self._id_lock = threading.Lock()
        self._session_id: str | None = None
        self._initialized = False

    # ---------- 基础 HTTP ----------

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def has_token(self) -> bool:
        return bool(self.token)

    def _post(self, payload: dict) -> list:
        """POST 一条 JSON-RPC 消息，返回解析出的消息列表。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"mcalmanac/{__version__}",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        req = urllib.request.Request(self.url + "/", data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                session_id = resp.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id
                body = resp.read().decode("utf-8", "replace")
                ctype = (resp.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if exc.code == 401:
                raise MCPAuthError(
                    "401：MCP Token 无效、已过期（服务端提示：" + (detail.strip()[:80] or "未提供")
                    + "）。请到 https://open.mcd.cn/mcp 登录后重新申请 Token，"
                    "并设置环境变量 MCD_MCP_TOKEN。"
                ) from None
            if exc.code == 403:
                raise MCPAuthError(
                    "403：请求被拒绝——大概率是未配置 MCP Token（服务端响应："
                    + (detail.replace("\n", " ").strip()[:120] or "无")
                    + "）。请到 https://open.mcd.cn/mcp 手机号登录 → 控制台 → 激活并复制 Token。"
                ) from None
            if exc.code == 429:
                raise MCPRateLimitError("429：触发限流（600 次/分钟），稍后再试。") from None
            raise MCPError(f"HTTP {exc.code}：{detail or exc.reason}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise MCPNetworkError(f"网络请求失败：{exc}") from None

        # 解析 JSON 或 SSE
        if "text/event-stream" in ctype:
            messages = _parse_sse(body)
        else:
            stripped = body.lstrip()
            if stripped.startswith("event:") or stripped.startswith("data:"):
                messages = _parse_sse(body)
            else:
                try:
                    obj = json.loads(body)
                except json.JSONDecodeError:
                    raise MCPError(f"无法解析服务器响应：{body[:200]}") from None
                messages = obj if isinstance(obj, list) else [obj]
        return [m for m in messages if isinstance(m, dict)]

    def _request(self, payload: dict, retries: int = 1) -> dict:
        """发送请求并返回与 id 匹配的那条消息。"""
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                messages = self._post(payload)
                for msg in messages:
                    if "id" in msg and msg.get("id") == payload.get("id"):
                        return msg
                if messages and "id" not in payload:
                    return messages[0]
                raise MCPError("服务器响应中缺少与请求匹配的消息。")
            except MCPNetworkError as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(0.8)
                    continue
                raise
        raise last_error or MCPError("未知错误")

    # ---------- MCP 协议层 ----------

    def initialize(self) -> dict:
        """执行 initialize 握手（仅在服务端要求会话时使用）。"""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcalmanac", "version": __version__},
            },
        }
        msg = self._request(payload)
        if "error" in msg:
            raise MCPError("initialize 失败：" + str(msg["error"]))
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except MCPError:
            pass
        self._initialized = True
        return msg.get("result", {})

    def list_tools(self) -> list:
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        msg = self._request(payload)
        if "error" in msg:
            raise MCPError("tools/list 失败：" + str(msg["error"]))
        return (msg.get("result") or {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None, check_error: bool = True):
        """调用一个 MCP Tool，返回 result 字典。

        check_error=True 时，业务错误（isError）会抛出 MCPError。
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        msg = self._request(payload)

        # 服务端要求先 initialize 的情况：补握手后重试一次
        if "error" in msg and not self._initialized:
            message = str((msg.get("error") or {}).get("message", "")).lower()
            if "session" in message or "initializ" in message:
                try:
                    self.initialize()
                except MCPError:
                    pass
                payload["id"] = self._next_id()
                msg = self._request(payload)

        if "error" in msg:
            raise MCPError(f"tools/call {name} 失败：{msg['error']}")

        result = msg.get("result") or {}
        if check_error and isinstance(result, dict) and result.get("isError"):
            raise MCPError(f"[{name}] {tool_text(result)[:300]}")
        return result

    def call_many(self, calls: list[tuple[str, dict | None]], max_workers: int = 6) -> dict:
        """并行调用多个 Tool，返回 {tool_name: result | Exception}，绝不抛出。"""
        results: dict[str, object] = {}

        def _run(item: tuple[str, dict | None]):
            name, args = item
            try:
                return name, self.call_tool(name, args or {})
            except MCPError as exc:
                return name, exc

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for name, outcome in pool.map(_run, calls):
                results[name] = outcome
        return results
