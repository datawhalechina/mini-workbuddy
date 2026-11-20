"""本章用的假模型：把"确认"和"提问"两条路都走一遍。

它按用户这句话里出现的关键词决定下一步，不依赖任何 API key：

    「写文件」  → 发起 Write 调用（触发权限确认）
    「选」/「哪个」 → 调用 ask_user（触发提问）
    其它        → 流式回一段预设文本

真正接模型时，把 build_model() 换回 OpenAIChatModel 即可，别的地方都不用动。
"""

import asyncio
import json
import os
import pathlib
from typing import Any, AsyncGenerator

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
)


def _last_user_text(messages: list[Msg]) -> str:
    for msg in reversed(messages):
        if getattr(msg, "role", "") == "user":
            return msg.get_text_content() or ""
    return ""


def _has_tool_result(messages: list[Msg]) -> bool:
    if not messages:
        return False
    blocks = messages[-1].get_content_blocks() or []
    return any(getattr(b, "type", None) == "tool_result" for b in blocks)


class ScriptedModel(ChatModelBase):
    def __init__(self, delay: float | None = None) -> None:
        self.model = "scripted"
        self.stream = True
        self.context_size = 32_000
        self.parameters = None
        self.max_retries = 0
        self.retry_delay = 0.0
        self.credential = None
        self.formatter = OpenAIChatFormatter()
        self.delay = delay if delay is not None else float(os.getenv("MOCK_DELAY", "0.04"))
        self.calls = 0
        self.did_write = False
        self.did_ask = False

    async def __call__(
        self,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        self.calls += 1
        call_id = f"scripted_{self.calls}"
        text = _last_user_text(messages)
        after_tool = _has_tool_result(messages)

        def one_block(block: Any) -> AsyncGenerator[ChatResponse, None]:
            async def stream_tool() -> AsyncGenerator[ChatResponse, None]:
                yield ChatResponse(
                    content=[block],
                    is_last=True,
                    id=call_id,
                    type="chat_response",
                    usage=ChatUsage(input_tokens=180, output_tokens=24, time=0.0),
                )

            return stream_tool()

        # ① 写文件：走权限确认
        if "写文件" in text and not self.did_write:
            self.did_write = True
            # 和第 12 章的 SANDBOX 保持一致：真模型写哪儿，假模型就写哪儿。
            # 用 abspath("./sandbox") 会跟着当前工作目录跑，换个目录跑就写飞了。
            target = str(pathlib.Path(__file__).resolve().parent / "sandbox" / "notes.md")
            return one_block(
                ToolCallBlock(
                    type="tool_call",
                    id=f"write_{self.calls}",
                    name="Write",
                    input=json.dumps(
                        {"file_path": target, "content": "# 待办\n\n- 补第 12 章\n- 截三张图\n"},
                        ensure_ascii=False,
                    ),
                ),
            )

        # ② 提问：走 ask_user
        if ("选" in text or "哪个" in text) and not self.did_ask:
            self.did_ask = True
            return one_block(
                ToolCallBlock(
                    type="tool_call",
                    id=f"ask_{self.calls}",
                    name="ask_user",
                    input=json.dumps(
                        {
                            "question": "这三张截图先拍哪一张？",
                            "options": ["确认弹窗", "选择题弹窗", "中断后的界面"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

        # ③ 拿到工具结果之后，说一句收尾的话
        if after_tool:
            body = (
                "收到。刚才那次工具调用已经回到我这里了，"
                "我把它当作事实继续往下走。\n\n"
                "这一轮结束。真实模型会在这里接着推理，假模型只负责把事件流跑完整。"
            )
        else:
            body = (
                f"收到你的输入「{text[:30]}」。\n\n"
                "这句话是预设的，用来验证确认、提问、中断三条通道能不能走通。"
            )

        async def stream_text() -> AsyncGenerator[ChatResponse, None]:
            pieces = [body[i:i + 8] for i in range(0, len(body), 8)]
            for i, piece in enumerate(pieces):
                await asyncio.sleep(self.delay)
                last = i == len(pieces) - 1
                yield ChatResponse(
                    content=[TextBlock(type="text", text=piece)],
                    is_last=last,
                    id=call_id,
                    type="chat_response",
                    usage=ChatUsage(input_tokens=180, output_tokens=len(piece), time=self.delay),
                    finished_reason=FinishedReason.COMPLETED if last else None,
                )

        return stream_text()
