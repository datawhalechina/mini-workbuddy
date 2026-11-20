"""没有 API key 时用的假模型。

它按句流式吐出预设文本，让 SSE 链路可以在本地完整跑通。
存在的意义：前后端联调不应该被一个 key 卡住。
"""

import asyncio
from typing import Any, AsyncGenerator

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
)


class MockModel(ChatModelBase):
    def __init__(self, delay: float = 0.05) -> None:
        self.model = "mock"
        self.stream = True
        self.context_size = 32_000
        self.parameters = None
        self.max_retries = 0
        self.retry_delay = 0.0
        self.credential = None
        self.formatter = OpenAIChatFormatter()
        self.delay = delay
        self.calls = 0
        self.wrote = False

    def _script(self, messages: list[Msg]) -> str:
        last = ""
        for msg in reversed(messages):
            if getattr(msg, "role", "") == "user":
                last = msg.get_text_content() or ""
                break
        return (
            f"收到你的问题：「{last[:40]}」。\n\n"
            "我是运行在本地假模型上的 Buddy，这段话是预设的，"
            "用来验证事件流能不能一段段送到浏览器。\n\n"
            "你现在看到的每一个字，都是后端推过来的一帧 SSE。"
        )

    @staticmethod
    def _last_user_text(messages: list[Msg]) -> str:
        for msg in reversed(messages):
            if getattr(msg, "role", "") == "user":
                return msg.get_text_content() or ""
        return ""

    async def __call__(
        self,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        self.calls += 1
        text = self._script(messages)
        call_id = f"mock_{self.calls}"

        # 说"写文件"就发起一个 Write 调用，用来演示确认流程
        if "写文件" in self._last_user_text(messages) and not self.wrote:
            import json as _json
            import os as _os

            self.wrote = True
            target = _os.path.abspath(_os.path.join("sandbox", "note.md"))

            async def tool_stream() -> AsyncGenerator[ChatResponse, None]:
                yield ChatResponse(
                    content=[ToolCallBlock(
                        type="tool_call",
                        id=f"call_{self.calls}",
                        name="Write",
                        input=_json.dumps(
                            {"file_path": target, "content": "这是假模型写进去的一行。"},
                            ensure_ascii=False,
                        ),
                    )],
                    is_last=True,
                    id=call_id,
                    type="chat_response",
                    usage=ChatUsage(input_tokens=120, output_tokens=20, time=0.0),
                )

            return tool_stream()

        # 注意：__call__ 本身是协程，它"返回"一个异步生成器。
        # 框架的调用方式是 await model(...)，然后对流式结果做 async for。
        async def stream() -> AsyncGenerator[ChatResponse, None]:
            chunks = [text[i:i + 8] for i in range(0, len(text), 8)]
            for i, piece in enumerate(chunks):
                await asyncio.sleep(self.delay)
                last = i == len(chunks) - 1
                yield ChatResponse(
                    content=[TextBlock(type="text", text=piece)],
                    is_last=last,
                    id=call_id,
                    type="chat_response",
                    usage=ChatUsage(
                        input_tokens=120, output_tokens=len(piece), time=self.delay,
                    ),
                    finished_reason=FinishedReason.COMPLETED if last else None,
                )

        return stream()
