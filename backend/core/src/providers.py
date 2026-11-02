"""Provider 层：把「DeepSeek API 的线上格式」和「agent 循环」隔开。

DeepSeek 走原生 Responses API（POST /responses，rule/deepseek-responses.md）。agent.py 只认三样东西：

    Reply(texts, tool_calls, usage)   模型这一轮说了什么 / 想调什么工具 / 花了多少 token
    ToolCall(id, name, input)  一次工具调用（已把 JSON 参数解析成 dict）
    ToolResult(call_id, content, is_error)

协议细节全部收在 DeepSeekProvider 里：
  system prompt   请求的 instructions 字段（不存进 conversation）
  历史            conversation 就是 input items：{"role": "user", ...} 和模型输出转成的 item
  工具声明        扁平的 {"type": "function", name, description, parameters}（没有嵌套的 function）
  assistant 回灌  reasoning / message / function_call 三种 item，按原顺序；带工具时 reasoning 必须回传
  工具结果回灌    每个结果一个 function_call_output item，call_id 与 function_call 配对
  修剪旧结果      只把旧 function_call_output 的 output 换成占位符，item 结构一个不动
  模型 / 强度     model、reasoning.effort；每次调用可以换（见 MODELS / EFFORTS）
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from tools import Tool

PRUNED_PREFIX = "[pruned to save context:"
MAX_OUTPUT_TOKENS = 8192  # 含可见文本和推理 token（思考模式要留足），第 4 步模型要吐整个文件
REQUEST_TIMEOUT = 120.0  # 秒
MAX_RETRIES = 3  # 429 / 5xx 的重试次数（不含第一次）；SDK 自己的重试关掉，免得叠加
RETRY_BASE_DELAY = 1.0  # 指数退避：1s、2s、4s ……

# 官方 Responses API 当前可选的模型和推理强度（https://api-docs.deepseek.com/api/create-response，
# 2026-11-02 核对）。强度 none 关闭思考；low / high / max 开启。模型和强度变了要回来改这里。
MODELS = ("deepseek-flash", "deepseek-v4-pro")
EFFORTS = ("none", "low", "high", "max")
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_EFFORT = "low"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    input_error: str | None = None  # 参数解析失败的原因；agent 不执行工具，把它当错误结果回灌


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int  # 这次请求发出去的全部上下文——它随轮数增长，就是上下文管理要管的东西
    output_tokens: int
    cached_tokens: int = 0  # 输入里命中服务端前缀缓存的部分（更便宜）


@dataclass(frozen=True)
class Reply:
    texts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None


class ProviderError(Exception):
    """模型调用没有正常完成：HTTP 错误、incomplete / failed、流中断、响应里有不认识的内容。

    status          "incomplete" / "failed" / "interrupted"，或 None（HTTP 错误）
    http_status     HTTP 状态码（有的话）
    partial_texts   已经收到的文字，调用方想保留就用它；工具调用绝不会放在这里执行
    """

    def __init__(
        self,
        message: str,
        *,
        status: str | None = None,
        http_status: int | None = None,
        partial_texts: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.http_status = http_status
        self.partial_texts = partial_texts or []


class Provider(Protocol):
    """agent 循环依赖的接口；测试里用 fake 实现替换。"""

    label: str  # 终端里打印的名字

    def chat(
        self,
        conversation: list[dict],
        tools: list[Tool],
        system: str = "",
        on_text: Callable[[str], None] | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> Reply:
        """发送 system + 整个 conversation；把 assistant 回复 append 进去；返回归一化的 Reply。
        system 怎么编码是各家协议的事，不存进 conversation。model / effort 不给就用 provider 自己的默认。"""

    def tool_results(self, results: list[ToolResult]) -> list[dict]:
        """把一轮的工具结果编码成要 append 进 conversation 的 item 列表。"""

    def prune_tool_results(self, conversation: list[dict], keep_last: int) -> int:
        """把最近 keep_last 个之外的旧工具结果换成占位符（原地修改），返回清理了几个。
        放在 provider 里，因为「工具结果长什么样」是线上格式的事。"""


def _check(value: str | None, allowed: tuple[str, ...], what: str) -> str:
    """请求发出去之前就拒绝不认识的取值：既不花钱，也比服务端的 400 更好懂。"""
    if value not in allowed:
        raise ValueError(f"unknown {what} {value!r}; allowed: {', '.join(allowed)}")
    return value


class DeepSeekProvider:
    label = "DeepSeek"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str = DEFAULT_EFFORT,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = _check(model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL), MODELS, "model")
        self.reasoning_effort = _check(reasoning_effort, EFFORTS, "reasoning effort")
        self._sleep = sleep
        if client is None:
            api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError("请先设置 DEEPSEEK_API_KEY（.env 或 export）；https://platform.deepseek.com")
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                timeout=REQUEST_TIMEOUT,
                max_retries=0,  # 重试由下面的 _create 负责；SDK 再重试一遍就叠加了
            )
        self.client = client

    @staticmethod
    def _tool_param(tool: Tool) -> dict:
        # Responses 的工具声明是扁平的，不是 Chat Completions 的 {"function": {...}}
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }

    def chat(
        self,
        conversation: list[dict],
        tools: list[Tool],
        system: str = "",
        on_text: Callable[[str], None] | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> Reply:
        """on_text 不为 None 时走流式：模型每吐出一段文字就调用一次；返回值和 conversation 里的内容与不流式时一样。

        只有整个响应 completed 才会把输出 append 进 conversation 并返回；incomplete / failed / 断流都抛
        ProviderError，conversation 保持原样（已收到的文字在 e.partial_texts 里）。
        """
        request: dict[str, Any] = {
            "model": _check(self.model if model is None else model, MODELS, "model"),
            "input": conversation,
            "reasoning": {
                "effort": _check(self.reasoning_effort if effort is None else effort, EFFORTS, "reasoning effort")
            },
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        if system:
            request["instructions"] = system
        if tools:
            request["tools"] = [self._tool_param(t) for t in tools]

        if on_text:
            response = self._read_stream(self._create(stream=True, **request), on_text)
        else:
            response = self._create(**request)
        self._require_completed(response, partial=self._texts(response))

        items = self._items(response)
        calls = [i for i in items if i["type"] == "function_call"]
        ids = [c["call_id"] for c in calls]
        for call_id in set(ids):
            if ids.count(call_id) > 1:  # 一个 call_id 只能对应一个结果，重复的没法正确回灌
                raise ProviderError(f"response contains the same call_id twice: {call_id}", status="failed")
        conversation.extend(items)

        texts = ["".join(p["text"] for p in i["content"]) for i in items if i["type"] == "message"]
        return Reply(
            texts=texts,
            tool_calls=[self._tool_call(c) for c in calls],
            usage=self._usage(response),
        )

    # --- 请求与重试 -------------------------------------------------------------------

    def _create(self, **request: Any) -> Any:
        """发一次请求。429 和临时 5xx 做有上限的指数退避；400/401/402 等不重试，直接说清原因。"""
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self.client.responses.create(**request)
            except Exception as e:
                status = getattr(e, "status_code", None)
                retryable = status == 429 or (isinstance(status, int) and status >= 500)
                if retryable and attempt < MAX_RETRIES:
                    self._sleep(RETRY_BASE_DELAY * 2**attempt)
                    continue
                raise self._http_error(e, status) from e
        raise AssertionError("unreachable")

    @staticmethod
    def _http_error(e: Exception, status: int | None) -> ProviderError:
        hint = {
            400: "请求格式或上下文有问题（检查历史里调用和结果是否配对、是否超出上下文窗口）",
            401: "API key 无效或缺失，请检查 backend/config.ini 的 api_key",
            402: "账户余额不足，请到 platform.deepseek.com 充值",
            429: "请求太频繁，重试后仍然被限流",
        }.get(status or 0)
        if hint is None and status and status >= 500:
            hint = "DeepSeek 服务暂时不可用，重试后仍然失败"
        text = f"调用 DeepSeek 失败（HTTP {status}）：{hint}" if hint else f"调用 DeepSeek 失败：{e}"
        return ProviderError(f"{text}。原始信息：{e}", http_status=status)

    # --- 响应解析 ---------------------------------------------------------------------

    @staticmethod
    def _require_completed(response: Any, partial: list[str]) -> None:
        status = getattr(response, "status", "completed")
        if status == "completed":
            return
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None) or "unknown"
            raise ProviderError(f"模型回复不完整（{reason}）", status="incomplete", partial_texts=partial)
        error = getattr(response, "error", None)
        raise ProviderError(
            f"模型调用失败：{getattr(error, 'message', None) or error or status}",
            status="failed",
            partial_texts=partial,
        )

    @staticmethod
    def _texts(response: Any) -> list[str]:
        """从 output 里的 message 提取答案（不假设 output[0] 是答案）。"""
        out = []
        for item in response.output:
            if item.type == "message":
                out.append("".join(p.text for p in item.content if p.type == "output_text"))
        return [t for t in out if t]

    @staticmethod
    def _items(response: Any) -> list[dict]:
        """把 output 里支持的 item 转成能原样放回 input 的 dict，顺序不变；不认识的类型明确报错。

        reasoning 只留明文 reasoning_text（summary、encrypted_content 输入端不支持）；没有推理内容就不造一个。
        """
        items: list[dict] = []
        for item in response.output:
            if item.type == "reasoning":
                text = "".join(p.text for p in (item.content or []) if p.type == "reasoning_text")
                if text:
                    items.append({"type": "reasoning", "content": [{"type": "reasoning_text", "text": text}]})
            elif item.type == "message":
                text = "".join(p.text for p in item.content if p.type == "output_text")
                if text:
                    items.append(
                        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
                    )
            elif item.type == "function_call":
                items.append(
                    {
                        "type": "function_call",
                        "call_id": item.call_id,
                        "name": item.name,
                        "arguments": item.arguments,
                    }
                )
            else:
                raise ProviderError(f"unsupported output item type: {item.type}", status="failed")
        return items

    @staticmethod
    def _usage(response: Any) -> Usage | None:
        u = getattr(response, "usage", None)
        if u is None:
            return None  # 缺失就是缺失，不当成 0
        details = getattr(u, "input_tokens_details", None)
        return Usage(u.input_tokens, u.output_tokens, getattr(details, "cached_tokens", 0) or 0)

    @staticmethod
    def _tool_call(item: dict) -> ToolCall:
        """arguments 是模型生成的 JSON 字符串，可能是坏的。不能抛异常：function_call 已经
        append 进 conversation，协议要求每个 call_id 都配一个 function_call_output。"""
        try:
            args = json.loads(item["arguments"] or "{}")
        except json.JSONDecodeError as e:
            return ToolCall(item["call_id"], item["name"], {}, input_error=f"arguments is not valid JSON: {e}")
        if not isinstance(args, dict):
            return ToolCall(item["call_id"], item["name"], {}, input_error="arguments must be a JSON object")
        return ToolCall(item["call_id"], item["name"], args)

    # --- 流式（SSE）-------------------------------------------------------------------

    @staticmethod
    def _read_stream(stream: Any, on_text: Callable[[str], None]) -> Any:
        """读 Responses 的 SSE 事件，文字边来边交给 on_text；返回终态事件里的完整 response。

        文字和思考按 output_index 分开攒，不会混拼不同 item；流式片段里的工具参数一律不执行，
        只认终态 response 里完整的 function_call。没有终态事件（断流）就是中断：保留已显示的文字，报错。
        """
        shown: list[str] = []
        for event in stream:
            kind = event.type
            if kind == "response.output_text.delta":
                shown.append(event.delta)
                on_text(event.delta)
            elif kind in ("response.completed", "response.incomplete", "response.failed"):
                return event.response
        raise ProviderError(
            "流式回复中途中断（没有收到结束事件）",
            status="interrupted",
            partial_texts=["".join(shown)] if shown else [],
        )

    # --- 工具结果与修剪 ---------------------------------------------------------------

    def tool_results(self, results: list[ToolResult]) -> list[dict]:
        # Responses 的 function_call_output 没有 is_error 字段，用前缀让模型知道这是失败。
        return [
            {
                "type": "function_call_output",
                "call_id": r.call_id,
                "output": f"Error: {r.content}" if r.is_error else r.content,
            }
            for r in results
        ]

    def prune_tool_results(self, conversation: list[dict], keep_last: int) -> int:
        # 不删 item，只换 output：删掉结果会让 call_id 落单（API 直接拒绝），
        # 也可能破坏 reasoning 的回传链。占位符还告诉模型「这里原来有东西，需要就重新调」。
        names = {m["call_id"]: m["name"] for m in conversation if m.get("type") == "function_call"}
        outputs = [m for m in conversation if m.get("type") == "function_call_output"]
        old = outputs[: max(len(outputs) - keep_last, 0)]
        pruned = 0
        for m in old:
            content = m["output"]
            if content.startswith(PRUNED_PREFIX):
                continue
            name = names.get(m["call_id"], "tool")
            placeholder = f"{PRUNED_PREFIX} earlier {name} result ({len(content):,} chars). Call it again if needed.]"
            if len(placeholder) >= len(content):
                continue  # 短结果换了反而更长
            m["output"] = placeholder
            pruned += 1
        return pruned
