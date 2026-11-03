"""第 6 章：自定义工具单独跑一遍，不经过模型。

    python code/ch06/tool_demo.py

需要装 agentscope，但不需要 API key。
"""

import asyncio

from pydantic import BaseModel, Field

from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


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


class ReportBuilder(ToolBase):
    """把几段材料拼成一份报告草稿。

    这是一个只读工具：只在内存里拼字符串，不写文件、不改状态。
    """

    def __init__(self) -> None:
        super().__init__()
        self.name = "build_report"
        self.description = (
            "把给定的章节名列表拼成一份报告骨架。"
            "当用户要求「生成报告」「搭个提纲」时使用。"
            "注意：本工具只生成骨架，不会写文件。"
        )
        self.input_schema = ReportInput.model_json_schema()
        self.is_read_only = True
        self.is_concurrency_safe = True

    async def check_permissions(self, **_kwargs) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="build_report 是只读工具，自动放行。",
        )

    async def call(self, **kwargs):
        args = ReportInput(**kwargs)

        lines = [f"# {args.topic}", ""]
        for i, section in enumerate(args.sections, 1):
            if args.style == "markdown":
                lines.append(f"## {i}. {section}")
            else:
                lines.append(f"{i}. {section}")
            lines.append("")

        yield ToolChunk(
            content=[TextBlock(text=f"正在生成 {len(args.sections)} 个章节…")],
            state="running",
            is_last=False,
        )

        await asyncio.sleep(0.05)

        yield ToolChunk(
            content=[TextBlock(text="\n".join(lines))],
            state="success",
            is_last=True,
        )


async def main() -> None:
    tool = ReportBuilder()

    print("--- 1. 工具的基本信息")
    print("name        =", tool.name)
    print("is_read_only=", tool.is_read_only)
    print("params      =", list(tool.input_schema["properties"]))
    print()

    print("--- 2. 问一句权限")
    decision = await tool.check_permissions()
    print("behavior =", decision.behavior)
    print("message  =", decision.message)
    print()

    print("--- 3. 真正调用，逐块看返回")
    async for chunk in tool.call(topic="本周工作", sections=["进展", "问题"]):
        print(f"[{chunk.state}] is_last={chunk.is_last}")
        print(chunk.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
