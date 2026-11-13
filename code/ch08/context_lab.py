"""第 8 章实验：上下文压缩到底省了多少 token。

不需要 API key。脚本里内联了一个按剧本回答的假模型，它不联网、不花钱、
每次跑出来的 token 数完全一样——这样 token 曲线的形状才可复现。

跑两种配置做对比：
  关闭压缩（窗口给到 100 万）vs 开启压缩（窗口 4000，触发线 80%）

运行：python context_lab.py
"""

import asyncio
from typing import Any

from agentscope.agent import Agent, ContextConfig
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
    StructuredResponse,
)
from agentscope.permission import PermissionMode
from agentscope.tool import FunctionTool, Toolkit

ROUNDS = 6
RESULT_CHARS = 400

SUMMARY_MARKER = "continuation summary"


class ScriptedModel(ChatModelBase):
    """按剧本回答的模型，用来替代真实 API。

    它只做三件事：
      1. 前 N 轮返回工具调用块
      2. 之后返回一段固定文本
      3. 收到摘要请求时，返回一份固定摘要

    为什么值得写这么个东西：换成真实模型，每一轮的输入 token 数会随
    网络、模型版本、甚至同一句话的重复调用而波动，压缩阈值被触发的时机
    也就跟着漂移——你看到的锯齿可能是噪声。假模型把输入固定住，锯齿就
    一定是框架动上下文造成的。

    Args:
        context_size: 假装自己的上下文窗口有多大
        tool_rounds: 前几轮用于发起工具调用
    """

    def __init__(
        self,
        context_size: int = 4000,
        tool_rounds: int = 6,
        tool_name: str = "fake_search",
    ) -> None:
        self.model = "scripted"
        self.stream = False
        self.context_size = context_size
        self.parameters = None
        self.max_retries = 0
        self.retry_delay = 0.0
        self.credential = None
        self.formatter = OpenAIChatFormatter()

        self._round = 0
        self._tool_rounds = tool_rounds
        self._tool_name = tool_name
        self.calls = 0
        self.input_tokens: list[int] = []

    def _is_summary_request(self, messages: list[Msg]) -> bool:
        for msg in messages:
            text = msg.get_text_content() or ""
            if SUMMARY_MARKER in text:
                return True
        return False

    async def __call__(
        self,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        self.input_tokens.append(await self.count_tokens(messages=messages, tools=tools))

        # 压缩时框架会来要摘要，这里必须返回一段文本，否则框架判定失败并退化为截断
        if self._is_summary_request(messages):
            return self._text_response(
                "已完成前 3 批检索，结果都在第 2 批里；下一步是汇总三批并输出报告。",
            )

        self._round += 1
        if self._round <= self._tool_rounds:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        type="tool_call",
                        id=f"call_{self._round}",
                        name=self._tool_name,
                        input=f'{{"keyword": "第{self._round}批"}}',
                    ),
                ],
                is_last=True,
                id=f"resp_{self._round}",
                type="chat_response",
                usage=ChatUsage(input_tokens=0, output_tokens=20, time=0.01),
            )

        return self._text_response("检索完成，共三批结果。")

    def _text_response(self, text: str) -> ChatResponse:
        return ChatResponse(
            content=[TextBlock(type="text", text=text)],
            is_last=True,
            id=f"resp_{self.calls}",
            type="chat_response",
            usage=ChatUsage(input_tokens=0, output_tokens=20, time=0.01),
            finished_reason=FinishedReason.COMPLETED,
        )

    async def generate_structured_output(
        self,
        messages: list[Msg],
        structured_model: Any,
        **kwargs: Any,
    ) -> StructuredResponse:
        """压缩上下文时框架走的是这条路，必须返回一份符合 schema 的内容。

        这里返回固定的五段摘要。真实模型会根据 compression_prompt 自己总结。
        """
        self.calls += 1
        self.input_tokens.append(
            await self.count_tokens(messages=messages, tools=None),
        )
        return StructuredResponse(
            content={
                "task_overview": "用户要求分 6 批检索资料，最后汇总成一份结果。",
                "current_state": "已完成前 3 批检索，原始结果分别落在第 1 至第 3 条工具结果里。",
                "important_discoveries": "每批检索结果约 1600 token，逐批累积后上下文增长很快。",
                "next_steps": "继续执行剩余批次，然后汇总输出。",
                "context_to_preserve": "回复用中文，保留具体数字（如批次数量）。",
            },
            id=f"struct_{self.calls}",
            type="structured_response",
            usage=ChatUsage(input_tokens=0, output_tokens=80, time=0.01),
            finished_reason=FinishedReason.COMPLETED,
        )


async def fake_search(keyword: str) -> str:
    """检索一批资料。

    Args:
        keyword: 检索关键词
    """
    return f"{keyword}：" + "结果内容。" * RESULT_CHARS


async def run(context_size: int, enable_compression: bool, label: str) -> None:
    model = ScriptedModel(context_size=context_size, tool_rounds=ROUNDS)

    context_config = ContextConfig(
        trigger_ratio=0.8,
        reserve_ratio=0.1,
        compression_fallback_to_truncation=enable_compression,
    )

    agent = Agent(
        name="Lab",
        system_prompt="你是检索助手。",
        model=model,
        toolkit=Toolkit(tools=[FunctionTool(fake_search)]),
        context_config=context_config,
    )
    agent.state.permission_context.mode = PermissionMode.BYPASS

    await agent.reply(UserMsg(name="user", content="帮我分六批检索资料，最后汇总。"))

    print(f"\n=== {label} ===")
    print("每次模型调用时的输入 token 数：")
    print("  " + " -> ".join(str(n) for n in model.input_tokens))
    print(f"峰值 {max(model.input_tokens)}　末值 {model.input_tokens[-1]}")
    print(f"上下文消息数 {len(agent.state.context)}　摘要长度 {len(agent.state.summary or '')}")
    if agent.state.summary:
        print("摘要：", agent.state.summary.replace("\n", " ")[:100])


async def main() -> None:
    await run(
        context_size=1_000_000,
        enable_compression=False,
        label="关闭压缩（大窗口）",
    )
    await run(
        context_size=4000,
        enable_compression=True,
        label="开启压缩（4k 窗口，触发线 3200）",
    )


if __name__ == "__main__":
    asyncio.run(main())
