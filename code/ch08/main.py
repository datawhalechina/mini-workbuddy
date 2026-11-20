"""第 8 章：别乱猜，先问一句。

模型拿不准的时候不要自己拍板。它给出几个方案，用户挑一个、再确认一次，
代码才动手。拒绝就一步不走，把结果告回模型，对话继续。
"""

import asyncio
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.event import EventType
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


# ---------------------------------------------------------------------------
# 要模型交出来的结构：一个问题 + 几个方案 + 一句理由
# ---------------------------------------------------------------------------
class ActionOption(BaseModel):
    """一个候选方案。"""

    label: str = Field(description="方案的短名，一句话以内")
    detail: str = Field(description="这个方案具体做什么，让用户看得懂后果")
    risk: str = Field(description="风险等级，涉及删除或外发时为高")


class ActionPlan(BaseModel):
    """模型给出的待拍板方案。"""

    question: str = Field(description="需要用户回答的问题")
    options: list[ActionOption] = Field(description="两到四个候选方案，按推荐程度排序")
    reason: str = Field(description="为什么这件事要交给用户决定，而不是模型自己定")


def build_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=API_KEY, base_url=BASE_URL or None),
        model=MODEL_NAME,
        context_size=32_768,
    )


def render(event) -> None:
    if event.type == EventType.TEXT_BLOCK_DELTA:
        print(getattr(event, "delta", ""), end="", flush=True)
    elif event.type == EventType.TOOL_CALL_START:
        print(f"\n  → {getattr(event, 'tool_call_name', '')}", flush=True)


async def build_agent(workspace: LocalWorkspace) -> Agent:
    tools = await workspace.list_tools()
    instructions = await workspace.get_instructions()

    agent = Agent(
        name="Buddy",
        system_prompt="你叫 Buddy，一个文件助手。拿不准的时候先给方案让用户选，不要自己定。\n\n"
        + instructions,
        model=build_model(),
        toolkit=Toolkit(tools=tools),
        offloader=workspace,
    )
    # 业务上的拍板由下面的菜单做，第 7 章那层安全确认在这个 demo 里跳过
    agent.state.permission_context.mode = PermissionMode.BYPASS
    return agent


def choose_option(plan: ActionPlan) -> ActionOption | None:
    """把模型给的选项摆出来，让用户挑一个。"""
    print(f"\n{plan.question}")
    print(f"模型的理由：{plan.reason}")
    for i, opt in enumerate(plan.options, 1):
        print(f"  [{i}] {opt.label}（风险 {opt.risk}）{opt.detail}")
    print("  [0] 都不做")

    raw = input("选一个编号 > ").strip()
    if raw == "0":
        return None
    return plan.options[int(raw) - 1]


def confirm_execution(option: ActionOption) -> bool:
    """再问一次，同意才往下走。"""
    answer = input(f"确认执行「{option.label}」？[y/N] ").strip().lower()
    return answer == "y"


async def main() -> None:
    os.makedirs(WORKDIR, exist_ok=True)

    async with LocalWorkspace(workdir=WORKDIR) as workspace:
        agent = await build_agent(workspace)

        # 一句含糊、又带点风险的请求：模型不知道你要哪个，该问而不是猜
        risky = "工作区里文件有点乱，帮我清一清，顺便把摘要发到 13900001111。"
        print(f">>> 你：{risky}\n")

        plan_msg = await agent.reply(
            UserMsg(name="user", content=risky),
            structured_schema=ActionPlan,
        )
        plan = ActionPlan(**plan_msg.structured_output)

        option = choose_option(plan)

        if option is None:
            print("\n[停止] 你选了都不做，代码一步没动。")
        elif not confirm_execution(option):
            print("\n[停止] 你没同意，代码一步没动。")
            reply = await agent.reply(
                UserMsg(name="user", content="刚才那几个方案我都不做，先别动文件。"),
            )
            print(f"<<< Buddy：{reply.get_text_content()}")
        else:
            print(f"\n[执行] 按「{option.label}」继续。")
            followup = await agent.reply(
                UserMsg(name="user", content=f"就按「{option.label}」这个方案做，其余别动。"),
            )
            print(f"<<< Buddy：{followup.get_text_content()}")


if __name__ == "__main__":
    asyncio.run(main())
