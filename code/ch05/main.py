"""第 5 章：给 Agent 一个工作区。

比上一章多了什么：
  1. LocalWorkspace：限定它能碰哪个目录
  2. 工具不再是手写的，而是工作区根据平台自动提供
  3. offloader：把上下文卸载到工作区
"""

import asyncio
import os

from dotenv import load_dotenv

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.message import UserMsg
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace

load_dotenv(override=True)

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

WORKDIR = "./sandbox"


def build_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=API_KEY, base_url=BASE_URL or None),
        model=MODEL_NAME,
    )


def prepare_files() -> None:
    """在工作区里放两份材料，供 Agent 阅读。"""
    os.makedirs(WORKDIR, exist_ok=True)
    with open(os.path.join(WORKDIR, "README.md"), "w", encoding="utf-8") as f:
        f.write(
            "# 项目说明\n\n"
            "这是一个用于演示工作区的示例项目。\n"
            "它包含三个模块：采集、清洗、导出。\n"
            "目前采集模块已完成，清洗模块在开发中。\n",
        )
    with open(os.path.join(WORKDIR, "todo.txt"), "w", encoding="utf-8") as f:
        f.write("1. 完成清洗模块\n2. 补充单元测试\n3. 写导出文档\n")


def tree(path: str) -> str:
    """打印目录树，用来确认 Agent 真的写了文件。"""
    lines = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {"__pycache__"}]
        level = root.replace(path, "").count(os.sep)
        lines.append("  " * level + os.path.basename(root) + "/")
        for name in sorted(files):
            lines.append("  " * (level + 1) + name)
    return "\n".join(lines)


async def main() -> None:
    prepare_files()

    # workspace 是异步上下文管理器：进入时初始化，退出时清理
    async with LocalWorkspace(workdir=WORKDIR) as workspace:
        tools = await workspace.list_tools()
        print("=" * 70)
        print("工作区提供了这些工具：", [t.name for t in tools])
        print("=" * 70)

        instructions = await workspace.get_instructions()
        print(f"工作区还会自动写一段系统提示（共 {len(instructions)} 字符），开头是这样：")
        print("-" * 70)
        print(instructions[:260].strip() + " ……")
        print("-" * 70)
        print()

        agent = Agent(
            name="Buddy",
            system_prompt=(
                "你叫 Buddy，是一个文件管理助手。"
                "所有文件操作都必须在当前工作区内完成，不要访问工作区以外的路径。"
                "\n\n" + instructions
            ),
            model=build_model(),
            toolkit=Toolkit(tools=tools),
            offloader=workspace,  # 上下文被压缩时，原文卸载到这里
        )

        # 命令行脚本没有确认界面，工作区的写操作默认会停下来等人点头。
        # 先整体放行；第 7 章会把这件事重新管起来。
        agent.state.permission_context.mode = PermissionMode.BYPASS

        question = (
            "看一下工作区里有哪些文件，把 README 的内容总结成三行，"
            "然后写入 summary.md。"
        )
        print(f">>> 你：{question}\n")
        reply = await agent.reply(UserMsg(name="user", content=question))
        print(f"<<< Buddy：{reply.get_text_content()}\n")

    # 退出 async with 会调用框架的 LocalWorkspace.close()：只关闭 MCP 连接，不删除工作区文件
    print("=" * 70)
    print(f"工作区当前结构（{WORKDIR}）：")
    print(tree(WORKDIR))


if __name__ == "__main__":
    asyncio.run(main())
