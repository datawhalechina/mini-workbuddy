"""第 10 章的三个中间件。

中间件的作用是在框架的关键节点插手，而不修改工具和 Agent 本身。
三个例子分别演示三种典型用途：
  AuditMiddleware   审计：记录每次模型调用和工具调用
  RedactMiddleware  脱敏：改工具入参，Mask 掉手机号
  StampMiddleware   改写 system prompt：把版本号盖上去
"""

import json
import re
import time
from collections.abc import AsyncIterable
from typing import Any, AsyncGenerator, Awaitable, Callable, Union

from agentscope.middleware import MiddlewareBase

PHONE = re.compile(r"1[3-9]\d{9}")


def tap_usage(response, on_usage):
    """从一次模型调用的返回值里取 usage，流式非流式都能处理。

    模型默认是流式返回，`next_handler()` 交回来的是一个**异步生成器**，
    此刻还没有任何分片产生，`response.usage` 自然读不到。真正的 usage
    挂在最后一个分片上，而中间件不能替调用方把流走完——那会把内容吞掉。
    所以流式这一支返回一个包装生成器：分片原样往下传，等最后一个分片
    经过时再回调 on_usage。
    """
    if not isinstance(response, AsyncIterable):
        # 非流式（含第 8、9 章的假模型）：直接就是一个 ChatResponse
        on_usage(getattr(response, "usage", None))
        return response

    async def wrapped():
        last = None
        async for chunk in response:
            last = chunk
            yield chunk
        on_usage(getattr(last, "usage", None))

    return wrapped()


class AuditMiddleware(MiddlewareBase):
    """把模型调用与工具调用记成一行行日志。

    状态存在 agent.state.middle_context 里，键是 get_middleware_key()。
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def on_model_call(
        self,
        agent,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable],
    ):
        start = time.time()

        def record(usage) -> None:
            self.records.append({
                "kind": "model_call",
                "elapsed_ms": round((time.time() - start) * 1000),
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            })

        return tap_usage(await next_handler(), record)

    async def on_acting(
        self,
        agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        tool_call = input_kwargs.get("tool_call")
        name = getattr(tool_call, "name", "?")
        raw = getattr(tool_call, "input", "")

        self.records.append({"kind": "tool_call", "tool": name, "input": raw})

        async for chunk in next_handler():
            yield chunk

        self.records.append({"kind": "tool_done", "tool": name})

    def dump(self) -> str:
        lines = []
        for r in self.records:
            if r["kind"] == "model_call":
                lines.append(
                    f"  [model] {r['elapsed_ms']:>5}ms  "
                    f"in={r['input_tokens']} out={r['output_tokens']}",
                )
            elif r["kind"] == "tool_call":
                lines.append(f"  [tool ] {r['tool']}  参数={r['input'][:40]}")
            else:
                lines.append(f"  [done ] {r['tool']}")
        return "\n".join(lines)


class RedactMiddleware(MiddlewareBase):
    """在工具执行前改参数：把手机号换成掩码。

    这是中间件最有价值的一类用法——同一个脱敏规则，
    对所有工具生效，不用在每个工具里各写一遍。
    """

    def __init__(self) -> None:
        self.hits = 0

    async def on_acting(
        self,
        agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        tool_call = input_kwargs.get("tool_call")
        raw = getattr(tool_call, "input", "") or ""

        masked, count = PHONE.subn(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], raw)
        if count:
            self.hits += count
            # ToolCallBlock 是 pydantic 模型，直接改字段即可
            tool_call.input = masked

        async for chunk in next_handler():
            yield chunk


class StampMiddleware(MiddlewareBase):
    """改写 system prompt：串行管道，直接返回新字符串。"""

    def __init__(self, stamp: str = "") -> None:
        self.stamp = stamp or time.strftime("%Y-%m-%d")

    # 虽然它只是个字符串变换，框架依然会 await 它，所以必须写成 async
    async def on_system_prompt(self, agent, current_prompt: str) -> str:
        return f"{current_prompt}\n\n<build>{self.stamp}</build>"
