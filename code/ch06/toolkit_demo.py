"""第 6 章：工具组单独跑一遍，不经过模型。

    python code/ch06/toolkit_demo.py

复用 main.py 里的 ReportBuilder 和 word_frequency，不需要 API key。
"""

import asyncio
import json

from agentscope.message import ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, ToolChunk, ToolGroup, Toolkit

from main import ReportBuilder, word_frequency


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


async def visible_tools(toolkit: Toolkit, state: AgentState) -> list[str]:
    """当前这一轮真正会发给模型的工具名。"""
    schemas = await toolkit.get_tool_schemas(state.tool_context.activated_groups)
    return [s["function"]["name"] for s in schemas]


async def call(toolkit: Toolkit, state: AgentState, name: str, payload: str) -> None:
    """模拟模型发起一次工具调用，只打印结果文本。"""
    tool_call = ToolCallBlock(id="call", name=name, input=payload)
    async for chunk in toolkit.call_tool(tool_call, state):
        # call_tool 最后还会吐一个 ToolResponse 汇总，这里只看过程块
        if isinstance(chunk, ToolChunk):
            print(f"[{chunk.state}] {chunk.content[0].text}")


async def main() -> None:
    toolkit = build_toolkit()
    state = AgentState()

    print("--- 1. 初始状态，模型看得见的工具")
    print(await visible_tools(toolkit, state))

    print("\n--- 2. reset_tools 的参数定义")
    for schema in await toolkit.get_tool_schemas(state.tool_context.activated_groups):
        if schema["function"]["name"] == "reset_tools":
            print(json.dumps(schema["function"]["parameters"], ensure_ascii=False, indent=2))

    print("\n--- 3. 还没激活就去调 build_report")
    await call(toolkit, state, "build_report", '{"topic": "本周工作"}')

    print("\n--- 4. 先激活 reporting，再调一次")
    await call(toolkit, state, "reset_tools", '{"reporting": true}')
    print("激活组 =", state.tool_context.activated_groups)
    print("可见工具 =", await visible_tools(toolkit, state))
    await call(toolkit, state, "build_report", '{"topic": "本周工作", "sections": ["进展", "问题"]}')

    print("\n--- 5. 传空参数，等于把所有组都关掉")
    await call(toolkit, state, "reset_tools", "{}")
    print("激活组 =", state.tool_context.activated_groups)
    print("可见工具 =", await visible_tools(toolkit, state))


if __name__ == "__main__":
    asyncio.run(main())
