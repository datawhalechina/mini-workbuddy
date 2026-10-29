"""第 13 章：把交互闭环补完。

比上一章多了什么：
  1. POST /api/confirm 从"是与否"升级成"允许一次 / 一直允许 / 拒绝"，
     并且允许把用户改过的参数交回给工具（ask_user 就靠这个传答案）
  2. POST /api/interrupt：两种打断，停在确认时发中断事件，跑着的时候取消任务
  3. 一个 ask_user 工具，借权限通道让模型"停下来问一句"

这个文件只负责"定义应用"：装配 Agent、挂路由、挂静态目录。
启动请用同目录的 main.py。

为什么单独拆出来：下一章要 `import` 它来复用路由，而两个文件都叫 main.py
会互相覆盖（`import main` 拿到的是自己）。
"""

import json
import os
import pathlib
import sys
import uuid
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ch11"))   # 复用第 11 章的运行时

from dotenv import load_dotenv                                    # noqa: E402
from fastapi import FastAPI, HTTPException                        # noqa: E402
from fastapi.responses import StreamingResponse                   # noqa: E402
from fastapi.staticfiles import StaticFiles                       # noqa: E402
from pydantic import BaseModel                                    # noqa: E402

from agentscope.agent import Agent                                # noqa: E402
from agentscope.credential import OpenAICredential                # noqa: E402
from agentscope.event import (                                    # noqa: E402
    ConfirmResult,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import ToolCallBlock                      # noqa: E402
from agentscope.model import OpenAIChatModel                      # noqa: E402
from agentscope.permission import (                               # noqa: E402
    PermissionBehavior,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from agentscope.tool import Toolkit, Write                        # noqa: E402

from runtime import Registry, Session, stream_events              # noqa: E402
from ask_user import AskUser                                      # noqa: E402

load_dotenv(override=True)

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
HEARTBEAT = float(os.getenv("SSE_HEARTBEAT", "15"))

# 工具的工作目录。提示词里要写绝对路径：Write 只认绝对路径。
SANDBOX = str(HERE / "sandbox")
os.makedirs(SANDBOX, exist_ok=True)


def build_model() -> Any:
    if os.getenv("MOCK") == "1" or not API_KEY:
        from fake_model import ScriptedModel

        return ScriptedModel()
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=API_KEY, base_url=BASE_URL or None),
        model=MODEL_NAME,
        context_size=32_768,
    )


def build_agent() -> Agent:
    """装配一个 Agent。"""
    agent = Agent(
        name="Buddy",
        system_prompt=(
            "你叫 Buddy，一个文件助手。短答即可。"
            "写文件用 Write。"
            # Write 只接受绝对路径，把沙箱的绝对路径直接告诉模型，
            # 省得它自己拼出 "./sandbox" 然后被工具拒绝。
            f"所有文件一律写到 {SANDBOX} 目录下。"
            "信息不足或需要用户在几个方案里挑一个时，调用 ask_user 提问，不要自己猜。"
        ),
        model=build_model(),
        toolkit=Toolkit(tools=[Write(), AskUser()]),
    )
    agent.state.permission_context.mode = PermissionMode.DEFAULT
    return agent


# ---------------------------------------------------------------------------
# 一、会话：在第 11 章的 Session 上只加一个方法
# ---------------------------------------------------------------------------
class InteractiveSession(Session):
    """多了"打断"能力的会话。"""

    async def start_reply(self, text: str) -> None:
        # 事件流里原本只有助手侧的内容，用户那句话前端是本地插进去的。
        # 一旦要支持"打开历史会话"（?session=xxx 回放），就会缺了提问的一半。
        # 所以把用户消息也编一帧发出去，前端按同一套逻辑渲染。
        self._emit("USER_MESSAGE", {"session_id": self.session_id, "text": text})
        await super().start_reply(text)

    async def interrupt(self) -> str:
        """两种打断，取决于此刻卡在哪。

        停在确认：本轮其实已经"结束"了（running 为 False），只是有待确认的调用。
                  这时不能 cancel 任务（没有任务可取消），要发 UserInterruptEvent，
                  框架会把待确认的调用标记为 interrupted 并收尾。
        正在跑：  直接取消后台任务。框架会捕获 CancelledError，
                  补一个 finished_reason=interrupted 的 REPLY_END 再退出。
        """
        if self.confirm_pending is not None:
            pending, self.confirm_pending = self.confirm_pending, None
            await self.start_resume(UserInterruptEvent(reply_id=pending.reply_id))
            return "已放弃本次确认"

        if self.task is not None and not self.task.done():
            self.task.cancel()
            return "已取消本次回复"

        return "当前没有进行中的任务"


class InteractiveRegistry(Registry):
    """换个会话类，其余照旧。"""

    def create(self, agent_factory) -> InteractiveSession:   # type: ignore[override]
        session = InteractiveSession(uuid.uuid4().hex[:12], agent_factory())
        self.sessions[session.session_id] = session
        return session


registry = InteractiveRegistry()


app = FastAPI(title="mini-workbuddy · ch13")


# ---------------------------------------------------------------------------
# 二、请求体
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str
    text: str


class ConfirmRequest(BaseModel):
    session_id: str
    tool_call_id: str
    decision: str = "allow"            # allow / always / deny
    input: dict[str, Any] | None = None  # 用户改过的参数；ask_user 的答案放这里


def _get(session_id: str) -> InteractiveSession:
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 三、路由
# ---------------------------------------------------------------------------
@app.post("/api/sessions")
async def create_session() -> dict[str, Any]:
    return {"session_id": registry.create(build_agent).session_id}


@app.get("/api/sessions")
async def list_sessions() -> dict[str, Any]:
    return {"items": registry.list()}


@app.post("/api/chat")
async def chat(body: ChatRequest) -> dict[str, Any]:
    session = _get(body.session_id)
    if session.running:
        raise HTTPException(status_code=409, detail="上一条回复还在进行中")
    await session.start_reply(body.text)
    return {"ok": True, "session_id": session.session_id}


@app.get("/api/stream/{session_id}")
async def stream(session_id: str, last_event_id: int = 0) -> StreamingResponse:
    session = _get(session_id)

    return StreamingResponse(
        stream_events(session, from_seq=last_event_id, heartbeat=HEARTBEAT),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/confirm")
async def confirm(body: ConfirmRequest) -> dict[str, Any]:
    """把用户在界面上的决定送回 Agent。

    三件事和上一章不同：
      · decision 有三种，always 会顺手写入一条 allow 规则，后面同类调用不再问
      · input 可以覆盖原始参数——这就是 ask_user 把答案带回去的通道
      · 参数用 Parse 后的 JSON 字符串回填，框架只认字符串
    """
    session = _get(body.session_id)
    pending = session.confirm_pending
    if pending is None:
        raise HTTPException(status_code=409, detail="当前没有待确认的调用")

    target = None
    for call in pending.tool_calls:
        if call.id == body.tool_call_id:
            target = call
            break
    if target is None:
        raise HTTPException(status_code=404, detail="这个调用不在待确认列表里")

    # 原始参数（框架里 input 是 JSON 字符串，不是 dict）
    args = json.loads(target.input) if isinstance(target.input, str) else dict(target.input or {})
    if body.input:
        args.update(body.input)

    rules: list[PermissionRule] = []
    if body.decision == "always":
        rule = PermissionRule(
            tool_name=target.name,
            rule_content="",                    # 空字符串 = 匹配这个工具的所有调用
            behavior=PermissionBehavior.ALLOW,
            source="user",
        )
        PermissionEngine(session.agent.state.permission_context).add_rule(rule)
        rules.append(rule)

    result = ConfirmResult(
        confirmed=body.decision != "deny",
        tool_call=ToolCallBlock(
            type="tool_call",
            id=target.id,
            name=target.name,
            input=json.dumps(args, ensure_ascii=False),
        ),
        rules=rules,
    )

    session.confirm_pending = None
    await session.start_resume(
        UserConfirmResultEvent(reply_id=pending.reply_id, confirm_results=[result]),
    )
    return {"ok": True, "decision": body.decision}


@app.post("/api/interrupt")
async def interrupt(body: dict[str, Any]) -> dict[str, Any]:
    session = _get(body.get("session_id", ""))
    return {"ok": True, "note": await session.interrupt()}


# 前端目录挂到根路径（路由顺序：先注册的优先，所以上面那些 API 不受影响）
app.mount("/", StaticFiles(directory=str(HERE), html=True), name="ui")


