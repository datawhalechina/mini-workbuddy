"""第 8 章离线演示：模型给选项，用户来拍板。

全程用假模型，不需要 API key。

    cd code/ch08 && python probe_ask.py
"""

import asyncio
import json
import pathlib
import sys
from typing import Any, Iterator

from pydantic import BaseModel, Field

from agentscope.agent import Agent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse, ChatUsage, FinishedReason

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


# ---------------------------------------------------------------------------
# 一、要模型交出来的结构
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


# ---------------------------------------------------------------------------
# 二、按剧本回答的假模型：见到结构化工具，就填一份方案清单
# ---------------------------------------------------------------------------
class ScriptedModel(ChatModelBase):
    def __init__(self) -> None:
        self.model = "scripted"
        self.stream = False
        self.context_size = 32_000
        self.parameters = None
        self.max_retries = 0
        self.retry_delay = 0.0
        self.credential = None
        self.formatter = OpenAIChatFormatter()
        self.calls = 0

    async def __call__(
        self,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        names = [t.get("function", {}).get("name") for t in (tools or [])]

        # 见到结构化工具，就填一份方案清单
        if "GenerateStructuredOutput" in names:
            data = {
                "question": "工作区里有一堆 .md 和 .tmp 文件，要怎么处理？",
                "options": [
                    {"label": "只列清单", "detail": "列出文件名，一个都不删", "risk": "低"},
                    {"label": "清掉临时文件", "detail": "只删 .tmp，不动 .md", "risk": "中"},
                    {
                        "label": "全删并外发摘要",
                        "detail": "删所有 .md，摘要发到 13900001111",
                        "risk": "高",
                    },
                ],
                "reason": "删文件和外发都不可撤销，得你定",
            }
            return ChatResponse(
                content=[ToolCallBlock(
                    type="tool_call", id="c1", name="GenerateStructuredOutput",
                    input=json.dumps(data, ensure_ascii=False),
                )],
                is_last=True, id="r1", type="chat_response",
                usage=ChatUsage(input_tokens=120, output_tokens=18, time=0.01),
            )

        return ChatResponse(
            content=[TextBlock(type="text", text="好的，按你说的办。")],
            is_last=True, id="r2", type="chat_response",
            usage=ChatUsage(input_tokens=180, output_tokens=12, time=0.01),
            finished_reason=FinishedReason.COMPLETED,
        )


# ---------------------------------------------------------------------------
# 三、两道关：选一个，再确认一次
# ---------------------------------------------------------------------------
def choose(plan: ActionPlan, answers: Iterator[str]) -> ActionOption | None:
    """把模型给的选项摆出来，让用户挑一个。"""
    print(f"  模型问：{plan.question}")
    for i, opt in enumerate(plan.options, 1):
        print(f"    [{i}] {opt.label}  风险{opt.risk}  {opt.detail}")
    raw = next(answers).strip().lower()
    if raw in ("0", "n", "no"):
        return None
    return plan.options[int(raw) - 1]


def confirm(option: ActionOption, answers: Iterator[str]) -> bool:
    """同意或不同意。选方案不等于批准执行，所以要多问一次。"""
    print(f"  再问一次：确认执行「{option.label}」？[y/N]")
    return next(answers).strip().lower() == "y"


async def plan_for(question: str) -> ActionPlan:
    """让模型出一份方案清单，结构化拿回来。"""
    agent = Agent(
        name="Buddy",
        system_prompt="你是一个文件助手。拿不准的时候给出候选方案，让用户选。",
        model=ScriptedModel(),
    )
    msg = await agent.reply(
        UserMsg(name="user", content=question),
        structured_schema=ActionPlan,
    )
    return ActionPlan(**msg.structured_output)


async def demo_approve() -> None:
    print("--- 1. 模型给三个方案，用户拍板后执行")
    plan = await plan_for("工作区里文件有点乱，帮我清一清。")
    print(f"  模型给的理由：{plan.reason}")
    answers = iter(["2", "y"])                  # 脚本化输入，换成 input() 就能真点
    option = choose(plan, answers)
    assert option is not None
    print(f"  用户选了：{option.label}")
    ok = confirm(option, answers)
    print(f"  用户回答：{'同意' if ok else '不同意'}")
    print(f"  执行：{option.detail}" if ok else "  执行：无，停在这一步")


async def demo_reject() -> None:
    print()
    print("--- 2. 同样三个方案，用户不同意")
    plan = await plan_for("工作区里文件有点乱，帮我清一清。")
    answers = iter(["3", "n"])
    option = choose(plan, answers)
    assert option is not None
    print(f"  用户选了：{option.label}")
    ok = confirm(option, answers)
    print(f"  用户回答：{'同意' if ok else '不同意'}")
    print("  执行：无，高风险方案停在确认这一步")


async def main() -> None:
    await demo_approve()
    await demo_reject()


if __name__ == "__main__":
    asyncio.run(main())
