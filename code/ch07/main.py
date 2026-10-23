"""第 7 章：别让它乱删东西。

比上一章多了什么：
  1. 权限模式：决定"默认放不放行"
  2. 人在环：模型申请调用工具时，先把球踢给用户
  3. 一个手写的确认循环（不依赖 launch_console）
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.event import (
    ConfirmResult,
    EventType,
    UserConfirmResultEvent,
)
from agentscope.message import UserMsg
from agentscope.model import OpenAIChatModel
from agentscope.permission import (
    PermissionBehavior,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
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


def render(event) -> None:
    """把事件渲染成终端输出。"""
    if event.type == EventType.TEXT_BLOCK_DELTA:
        print(getattr(event, "delta", ""), end="", flush=True)
    elif event.type == EventType.TOOL_CALL_START:
        print(f"\n  → 调用 {getattr(event, 'tool_call_name', '')}", flush=True)
    elif event.type == EventType.TOOL_RESULT_END:
        print(f"    完成（{getattr(event, 'state', '')}）", flush=True)


async def run_with_confirmation(agent: Agent, question: str) -> None:
    """跑一轮回复；遇到需要确认的工具调用，就地问用户。"""
    inputs = UserMsg(name="user", content=question)
    print(f">>> 你：{question}")

    while True:
        pending = None

        async for event in agent.reply_stream(inputs):
            if event.type == EventType.REQUIRE_USER_CONFIRM:
                pending = event
                break
            render(event)

        # 没有待确认项，说明这一轮结束了
        if pending is None:
            print("\n")
            return

        # 有待确认项：逐个问用户
        results = []
        for tool_call in pending.tool_calls:
            print(f"\n  ⚠ 需要确认：{tool_call.name}")
            print(f"    参数：{tool_call.input}")
            answer = input("    允许执行？[y] 允许 / [n] 拒绝 / [a] 始终允许 > ")
            answer = answer.strip().lower()

            # 选 a：把这一次的批准固化成一条 allow 规则，下次不再问
            rules = []
            if answer == "a":
                rule = PermissionRule(
                    tool_name=tool_call.name,
                    rule_content="",
                    behavior=PermissionBehavior.ALLOW,
                    source="user",
                )
                PermissionEngine(agent.state.permission_context).add_rule(rule)
                rules.append(rule)
                print(f"    已记住：今后 {tool_call.name} 直接放行")

            results.append(
                ConfirmResult(
                    confirmed=answer in ("y", "a"),
                    tool_call=tool_call,
                    rules=rules,
                ),
            )

        # 把确认结果交回去，Agent 从暂停处继续
        inputs = UserConfirmResultEvent(
            reply_id=pending.reply_id,
            confirm_results=results,
        )


async def main() -> None:
    os.makedirs(WORKDIR, exist_ok=True)

    async with LocalWorkspace(workdir=WORKDIR) as workspace:
        tools = await workspace.list_tools()
        instructions = await workspace.get_instructions()

        agent = Agent(
            name="Buddy",
            system_prompt=(
                "你叫 Buddy，是一个文件管理助手。所有文件操作都在当前工作区内完成。"
                "\n\n" + instructions
            ),
            model=build_model(),
            toolkit=Toolkit(tools=tools),
            offloader=workspace,
        )

        # 权限模式：决定"默认放不放行"
        agent.state.permission_context.mode = PermissionMode.DEFAULT
        print("=" * 70)
        print(f"当前权限模式：{agent.state.permission_context.mode.value}")
        print("说明：DEFAULT 模式下，会写文件的操作需要你确认。")
        print("=" * 70)
        print()

        await run_with_confirmation(
            agent,
            "在工作区里创建一个 notes.md，写入三行：今天做了什么、遇到什么问题、明天计划。",
        )


if __name__ == "__main__":
    asyncio.run(main())
