"""第 6 章：工具不够用，就自己造。

比上一章多了什么：
  1. 继承 ToolBase 写一个完整工具（能流式返回、能声明只读）
  2. 用 Pydantic 定义参数 schema
  3. 工具组：让智能体运行时自己决定要不要启用
"""

import asyncio
import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.message import TextBlock, UserMsg
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolChunk,
    ToolGroup,
    Toolkit,
)

load_dotenv(override=True)

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def build_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=API_KEY, base_url=BASE_URL or None),
        model=MODEL_NAME,
    )


# ---------------------------------------------------------------------------
# 一、用 Pydantic 定义参数，比裸 dict 清楚得多
# ---------------------------------------------------------------------------


class ReportInput(BaseModel):
    """生成报告的参数。"""

    topic: str = Field(description="报告主题，例如「本周工作」")
    sections: list[str] = Field(
        default_factory=list,
        description="报告的章节名列表，按顺序输出",
    )
    style: str = Field(
        default="markdown",
        description="输出格式，可选 markdown 或 plain",
    )


# ---------------------------------------------------------------------------
# 二、继承 ToolBase 的完整工具
# ---------------------------------------------------------------------------


class ReportBuilder(ToolBase):
    """把几段材料拼成一份报告草稿。

    这是一个只读工具：它只在内存里拼字符串，不写文件、不改状态。
    """

    def __init__(self) -> None:
        super().__init__()
        self.name = "build_report"
        self.description = (
            "把给定的章节名列表拼成一份报告骨架。"
            "当用户要求「生成报告」「搭个提纲」时使用。"
            "注意：本工具只生成骨架，不会写文件。"
        )
        # 用 Pydantic 模型的 JSON Schema 作为参数定义
        self.input_schema = ReportInput.model_json_schema()
        self.is_read_only = True
        self.is_concurrency_safe = True

    async def check_permissions(self, *_args, **_kwargs) -> PermissionDecision:
        """只读工具，不需要用户确认。

        框架会按位置传进 tool_input 和 context 两个参数，
        这里先用 *_args 全收下。
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="build_report 是只读工具，自动放行。",
        )

    async def call(self, **kwargs) -> AsyncGenerator[ToolChunk, None]:
        """执行逻辑。可以分多次 yield，实现进度反馈。"""
        args = ReportInput(**kwargs)

        lines = [f"# {args.topic}", ""]
        for i, section in enumerate(args.sections, 1):
            if args.style == "markdown":
                lines.append(f"## {i}. {section}")
            else:
                lines.append(f"{i}. {section}")
            lines.append("")

        # 先吐一句进度，让界面知道开始了
        yield ToolChunk(
            content=[TextBlock(text=f"正在生成 {len(args.sections)} 个章节…")],
            state="running",
            is_last=False,
        )

        # 模拟一点耗时，让流式效果可见
        await asyncio.sleep(0.05)

        yield ToolChunk(
            content=[TextBlock(text="\n".join(lines))],
            state="success",
            is_last=True,
        )


# ---------------------------------------------------------------------------
# 三、普通函数工具：轻量场景够用
# ---------------------------------------------------------------------------


def word_frequency(text: str, top_n: int = 5) -> str:
    """统计一段文本里出现频率最高的词。

    Args:
        text: 待统计的文本。
        top_n: 返回前几名，默认 5。
    """
    import re
    from collections import Counter

    words = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", text.lower())
    if not words:
        return "没有统计到有效的词。"
    counter = Counter(words).most_common(max(1, min(top_n, 20)))
    return "、".join(f"{w}×{c}" for w, c in counter)


# ---------------------------------------------------------------------------
# 四、装配：常驻工具和按需工具分开
# ---------------------------------------------------------------------------


def build_toolkit() -> Toolkit:
    return Toolkit(
        tools=[
            FunctionTool(word_frequency),
        ],
        tool_groups=[
            ToolGroup(
                name="reporting",
                description="报告生成相关工具。用户提到报告、提纲、周报时启用。",
                instructions="生成的报告只是骨架，需要用户补充内容。",
                tools=[ReportBuilder()],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 五、回放这一轮的工具调用，让读者看见模型干了什么
# ---------------------------------------------------------------------------


def block_text(block) -> str:
    """把工具返回的内容块压成一行文本。"""
    if isinstance(block, str):
        return block
    parts = [getattr(item, "text", "") for item in block or []]
    return " ".join(p for p in parts if p)


def print_trace(state: AgentState) -> None:
    """按时间顺序打印本轮所有工具调用和返回。"""
    for msg in state.context:
        for block in msg.content:
            if block.type == "tool_call":
                print(f"  -> {block.name} {block.input}")
            elif block.type == "tool_result":
                text = block_text(block.output).replace("\n", " ")
                print(f"  <- [{block.state}] {text[:100]}")


async def main() -> None:
    toolkit = build_toolkit()

    print("=" * 70)
    print("初始状态：只有 basic 组的工具可见")
    for schema in await toolkit.get_tool_schemas():
        fn = schema.get("function", schema)
        print("  ·", fn.get("name"))
    print("=" * 70)
    print()

    agent = Agent(
        name="Buddy",
        system_prompt=(
            "你叫 Buddy。遇到报告、提纲、周报这类请求时，"
            "必须先调用 reset_tools 查看工具组、激活 reporting 组，"
            "再用 build_report 生成骨架，不要自己手写提纲。短答即可。"
        ),
        model=build_model(),
        toolkit=toolkit,
    )

    question = "帮我搭一份「本周工作」的报告提纲，包含进展、问题、下周计划三部分。"
    print(f">>> 你：{question}\n")
    reply = await agent.reply(UserMsg(name="user", content=question))
    print(f"<<< Buddy：{reply.get_text_content()}\n")

    print("=" * 70)
    print("这一轮模型实际做了什么（按时间顺序）")
    print_trace(agent.state)
    print("=" * 70)
    print("最终激活的工具组：", agent.state.tool_context.activated_groups)


if __name__ == "__main__":
    asyncio.run(main())
