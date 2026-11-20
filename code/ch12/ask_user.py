"""ask_user：让模型有能力停下来问一句。

它不执行任何业务逻辑，存在的价值只有一个——把控制权交回给人。

做法有点绕，但完全建立在第 7 章的权限系统之上：
  1. check_permissions() 永远返回 ASK，于是本轮停在 REQUIRE_USER_CONFIRM
  2. 用户在界面上选了答案，前端把答案塞进 tool_call.input 的 answer 字段交回来
  3. 框架会用交回来的 input 覆盖原始参数（"用户可修改参数"是官方支持的行为），
     再用它调用 call()
  4. call() 把答案原样返回给模型，模型继续推理

注意两个坑，都在实测里踩到了：
  · is_read_only 必须为 False。声明只读的工具会走"只读放行"快路径，
    check_permissions() 根本不会被调用。
  · check_permissions 的签名是 (self, tool_input, context)，位置参数，
    写成 **kwargs 会在被调用的那一刻抛 TypeError。
"""

from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


class AskUserInput(BaseModel):
    question: str = Field(description="要向用户提出的问题，一句话说清")
    options: list[str] = Field(
        default_factory=list,
        description="候选项。给了候选项用户就点按钮，留空则自由作答",
    )


class AskUser(ToolBase):
    """向用户提问并等待回答。"""

    def __init__(self) -> None:
        super().__init__()
        self.name = "ask_user"
        self.description = (
            "当信息不足、或需要在几个方案之间做选择时，向用户提问并等待回答。"
            "把可能的选项写进 options，用户点击比你猜要快得多。"
            "不要在能自己判断的时候调用它——提问会中断当前一轮。"
        )
        self.input_schema = AskUserInput.model_json_schema()
        # 必须为 False：只读工具会被权限引擎直接放行，走不到 check_permissions
        self.is_read_only = False
        self.is_concurrency_safe = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        """永远 ASK：这不是安全检查，而是借权限通道实现"停下来等人"。"""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="需要用户回答后才能继续",
            decision_reason="ask_user 主动暂停本轮",
            bypass_immune=True,       # 即使用户选了"全部放行"也要问
        )

    async def call(self, **kwargs: Any) -> AsyncGenerator[ToolChunk, None]:
        """把用户的回答原样交回给模型。

        参数里的 answer 不是模型填的，是界面上交回来的。
        """
        args = AskUserInput(**{k: v for k, v in kwargs.items() if k != "answer"})
        answer = str(kwargs.get("answer") or "").strip()

        if not answer:
            yield ToolChunk(
                content=[TextBlock(text="用户没有作答。请换一种方式继续，或直接给出建议。")],
                state="error",
                is_last=True,
            )
            return

        yield ToolChunk(
            content=[TextBlock(text=f"用户对「{args.question}」的回答：{answer}")],
            state="success",
            is_last=True,
        )
