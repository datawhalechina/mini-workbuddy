"""第 10 章：在关键节点插手。

本章的重点是让人插手。模型给出候选方案，用户拍板之后代码才动手。
中间件是另一类插手，由框架在后台记账、脱敏、控预算。
"""

import asyncio
import os
import time
from typing import Any, Callable

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.event import EventType
from agentscope.message import UserMsg
from agentscope.middleware import MiddlewareBase
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace

from middlewares import (
    AuditMiddleware,
    RedactMiddleware,
    StampMiddleware,
    tap_usage,
)


load_dotenv(override=True)

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

WORKDIR = "./sandbox"


# ---------------------------------------------------------------------------
# 一类插手：框架在后台干活，用户看不见
# ---------------------------------------------------------------------------
class BudgetMiddleware(MiddlewareBase):
    """给一次会话设 token 上限。"""

    def __init__(self, limit: int = 60_000, price_per_k: float = 0.002) -> None:
        self.limit = limit
        self.price_per_k = price_per_k
        self.used = 0
        self.exceeded = False
        self.spent = 0.0

    async def on_model_call(
        self,
        agent,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ):
        if self.used >= self.limit:
            self.exceeded = True
            raise RuntimeError(
                f"预算已用尽：{self.used}/{self.limit} tokens。"
                "请新建会话，或调高上限。",
            )

        def charge(usage) -> None:
            if usage is None:
                return
            tokens = usage.input_tokens + usage.output_tokens
            self.used += tokens
            self.spent += tokens / 1000 * self.price_per_k

        return tap_usage(await next_handler(), charge)

    def report(self) -> str:
        return (
            f"用量 {self.used}/{self.limit} tokens，"
            f"估算花费 {self.spent:.4f} 元"
        )


# ---------------------------------------------------------------------------
# 另一类插手：模型只出方案，拍板权在人手里
# ---------------------------------------------------------------------------
class ActionOption(BaseModel):
    """一个候选方案。"""

    label: str = Field(description="方案的短名，一句话以内")
    detail: str = Field(description="这个方案具体做什么，让用户看得懂后果")
    risk: str = Field(description="风险等级，低 / 中 / 高")


class ActionPlan(BaseModel):
    """模型给出的待拍板方案。"""

    question: str = Field(description="需要用户拍板的问题")
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


async def build_agent(workspace: LocalWorkspace):
    tools = await workspace.list_tools()
    instructions = await workspace.get_instructions()

    audit = AuditMiddleware()
    redact = RedactMiddleware()
    stamp = StampMiddleware(stamp="mini-workbuddy/ch10")
    budget = BudgetMiddleware(limit=60_000)

    agent = Agent(
        name="Buddy",
        system_prompt="你叫 Buddy，一个文件助手。\n\n" + instructions,
        model=build_model(),
        toolkit=Toolkit(tools=tools),
        offloader=workspace,
        # 顺序即执行顺序，先脱敏，审计看到的就已经是掩码
        middlewares=[redact, audit, budget, stamp],
    )
    # 业务上的拍板由下面的菜单做，第 7 章那层安全确认在这个 demo 里跳过
    agent.state.permission_context.mode = PermissionMode.BYPASS
    return agent, audit, budget


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
        agent, audit, budget = await build_agent(workspace)

        # ---- 第一段：普通对话，中间件在旁边记流水 ----
        print("=" * 70)
        print("一、普通对话")
        print("=" * 70)
        question = "看看工作区里有什么文件，用一句话告诉我。"
        print(f">>> 你：{question}\n")

        start = time.time()
        msg = await agent.reply(UserMsg(name="user", content=question))
        print(f"<<< Buddy：{msg.get_text_content()}")
        print(f"\n耗时 {time.time() - start:.1f}s")

        print("\n中间件记录：")
        print(audit.dump())
        print("预算：", budget.report())

        # ---- 第二段：模型出方案，用户拍板 ----
        print()
        print("=" * 70)
        print("二、模型出方案，用户拍板")
        print("=" * 70)

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
        else:
            print(f"\n[执行] 按「{option.label}」继续。")
            followup = await agent.reply(
                UserMsg(name="user", content=f"就按「{option.label}」这个方案做，其余别动。"),
            )
            print(f"<<< Buddy：{followup.get_text_content()}")

        print("\n预算：", budget.report())


if __name__ == "__main__":
    asyncio.run(main())
