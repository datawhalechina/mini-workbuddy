"""第 10 章离线演示：模型给选项，用户来拍板。

默认跑前两段，本章主线。加 --middleware 跑后两段，看另一类插手。

    cd code/ch10 && python probe_middleware.py
    cd code/ch10 && python probe_middleware.py --middleware
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
from agentscope.permission import PermissionMode
from agentscope.tool import FunctionTool, Toolkit

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from middlewares import (  # noqa: E402
    AuditMiddleware,
    RedactMiddleware,
    StampMiddleware,
)


class ActionOption(BaseModel):
    """一个候选方案。"""

    label: str = Field(description="方案的短名，一句话以内")
    detail: str = Field(description="这个方案具体做什么")
    risk: str = Field(description="风险等级，低 / 中 / 高")


class ActionPlan(BaseModel):
    """模型给出的待拍板方案。"""

    question: str = Field(description="需要用户拍板的问题")
    options: list[ActionOption] = Field(description="两到四个候选方案，按推荐程度排序")
    reason: str = Field(description="为什么这件事要交给用户决定")


class MeetingSummary(BaseModel):
    """会议纪要，10.3 用它讲结构化输出的机制。"""

    title: str = Field(description="会议主题")
    decisions: list[str] = Field(description="达成的决定")
    attendees: int = Field(description="参会人数")


class ScriptedModel(ChatModelBase):
    """按剧本回答：给出三个候选方案；中间件演示时发一次短信。"""

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

        # 中间件演示：第一轮发一次短信
        if self.calls == 1 and "send_sms" in names:
            return ChatResponse(
                content=[ToolCallBlock(
                    type="tool_call", id="c2", name="send_sms",
                    input=json.dumps(
                        {"phone": "13812345678", "text": "明天九点开会"},
                        ensure_ascii=False,
                    ),
                )],
                is_last=True, id="r2", type="chat_response",
                usage=ChatUsage(input_tokens=120, output_tokens=18, time=0.01),
            )

        return ChatResponse(
            content=[TextBlock(type="text", text="短信已发出。")],
            is_last=True, id="r3", type="chat_response",
            usage=ChatUsage(input_tokens=180, output_tokens=12, time=0.01),
            finished_reason=FinishedReason.COMPLETED,
        )


async def send_sms(phone: str, text: str) -> str:
    """给指定手机号发短信。

    Args:
        phone: 手机号
        text: 短信内容
    """
    return f"已发送给 {phone}：{text}"


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
    """同意或不同意，第 7 章那道闸机在命令行里的样子。"""
    print(f"  再问一次：确认执行「{option.label}」？[y/N]")
    return next(answers).strip().lower() == "y"


async def plan_for(question: str) -> ActionPlan:
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


def make_agent(audit: AuditMiddleware, order: list) -> Agent:
    agent = Agent(
        name="Buddy",
        system_prompt="你是一个通知助手。",
        model=ScriptedModel(),
        toolkit=Toolkit(tools=[FunctionTool(send_sms)]),
        middlewares=order,
    )
    agent.state.permission_context.mode = PermissionMode.BYPASS
    return agent


async def demo_middlewares() -> None:
    print()
    print("--- 3. 换一个角度，中间件在旁边记流水")
    audit = AuditMiddleware()
    agent = make_agent(audit, [RedactMiddleware(), audit, StampMiddleware(stamp="ch10-demo")])
    msg = await agent.reply(UserMsg(name="user", content="给 13812345678 发条短信"))
    print(f"  回复：{msg.get_text_content()}")
    for line in audit.dump().splitlines():
        print(f"  {line}")


async def demo_order() -> None:
    print()
    print("--- 4. 只换注册顺序，审计记下的是哪个号码")
    for order, label in (
        ([AuditMiddleware(), RedactMiddleware()], "先审计，后脱敏"),
        ([RedactMiddleware(), AuditMiddleware()], "先脱敏，后审计"),
    ):
        audit = next(m for m in order if isinstance(m, AuditMiddleware))
        agent = make_agent(audit, order)
        await agent.reply(UserMsg(name="user", content="给 13812345678 发条短信"))
        print(f"  {label}：{audit.records[1]['input'][:34]}")


async def main() -> None:
    if "--middleware" in sys.argv:
        await demo_middlewares()
        await demo_order()
    else:
        await demo_approve()
        await demo_reject()


if __name__ == "__main__":
    asyncio.run(main())
