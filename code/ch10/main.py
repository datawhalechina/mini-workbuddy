"""第 10 章：在关键节点插手。

比上一章多了什么：
  1. 中间件：在模型调用、工具调用、system prompt 三个节点插手，不改 Agent 也不改工具
  2. 结构化输出：让模型必须返回一份符合 schema 的数据，用于任务分级
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
# 第三个中间件：预算控制。超出上限时不再调用模型，直接返回一句提示。
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

        # 与审计中间件同样的处理：流式响应要等最后一个分片流过才看得到
        # usage，所以扣费放在包装生成器的收尾处。
        return tap_usage(await next_handler(), charge)

    def report(self) -> str:
        return (
            f"用量 {self.used}/{self.limit} tokens，"
            f"估算花费 {self.spent:.4f} 元"
        )


# ---------------------------------------------------------------------------
# 结构化输出：任务分级
# ---------------------------------------------------------------------------
class TaskRouting(BaseModel):
    """对用户请求做分级，决定走哪条处理路径。"""

    level: str = Field(description="复杂度：simple / normal / complex")
    needs_tools: bool = Field(description="是否需要调用工具")
    risk: str = Field(description="风险等级：low / medium / high，涉及删除或外发时为 high")
    reason: str = Field(description="一句话说明判断依据")
    steps: list[str] = Field(description="建议的处理步骤，最多四条")


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
        # 顺序即执行顺序：先脱敏，审计看到的就已经是掩码
        middlewares=[redact, audit, budget, stamp],
    )
    agent.state.permission_context.mode = PermissionMode.BYPASS
    return agent, audit, budget


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

        # ---- 第二段：结构化输出，先分级再动手 ----
        print()
        print("=" * 70)
        print("二、结构化输出：先判断该不该动手")
        print("=" * 70)

        risky = "把工作区里所有 .md 文件删掉，然后把内容摘要发到 13900001111。"
        print(f">>> 你：{risky}\n")

        # structured_schema 只约束最终输出，不会禁用已有工具。
        # 分级使用独立的无工具 Agent，避免判断风险之前就执行了危险请求。
        routing_agent = Agent(
            name="Router",
            system_prompt="你只负责判断请求的复杂度与风险，不执行请求。涉及删除或外发时，risk 为 high。",
            model=build_model(),
            middlewares=[audit, budget],  # 两段调用仍计入同一份审计和预算
        )
        routing_msg = await routing_agent.reply(
            UserMsg(name="user", content=risky),
            structured_schema=TaskRouting,
        )

        routing = TaskRouting(**routing_msg.structured_output)
        print("分级结果：")
        print(f"  复杂度：{routing.level}")
        print(f"  需要工具：{routing.needs_tools}")
        print(f"  风险：{routing.risk}")
        print(f"  依据：{routing.reason}")
        print("  步骤：")
        for i, step in enumerate(routing.steps, 1):
            print(f"    {i}. {step}")

        # 这一层判断是代码做的，不是模型做的 —— 高危请求直接拦死
        if routing.risk == "high":
            print("\n[拦截] 风险等级 high，不执行，转人工审批。")

        print("\n预算：", budget.report())


if __name__ == "__main__":
    asyncio.run(main())
