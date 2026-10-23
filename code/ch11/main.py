"""第 11 章：把 Agent 包成一个 HTTP 服务。

启动：
    python main.py
    # 或者 uvicorn main:app --port 8848 --reload

五个接口：
    POST /api/sessions            新建会话
    GET  /api/sessions            会话列表
    POST /api/chat                发起一轮回复（立刻返回，不等结果）
    GET  /api/stream/{id}         SSE 事件流
    POST /api/confirm             回复一次确认请求
"""

import asyncio
import json
import os
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.event import ConfirmResult, UserConfirmResultEvent
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import Toolkit, Write

from runtime import Registry, Session

load_dotenv(override=True)

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

HERE = os.path.dirname(os.path.abspath(__file__))

# 工具的工作目录。给模型的提示里必须写绝对路径——
# Write 只认绝对路径，写 "./sandbox/x.md" 会被直接拒绝。
SANDBOX = os.path.join(HERE, "sandbox")
os.makedirs(SANDBOX, exist_ok=True)

# 心跳间隔（秒）。代理通常会掐掉 60 秒无数据的连接。
HEARTBEAT = float(os.getenv("SSE_HEARTBEAT", "15"))
registry = Registry()


# ---------------------------------------------------------------------------
# 模型：有 key 用真模型，没 key 用假模型，保证服务一定能起来
# ---------------------------------------------------------------------------
def build_model():
    # 想强制用假模型联调，启动前设置 MOCK=1
    if os.getenv("MOCK") == "1" or not API_KEY:
        from mock_model import MockModel
        print("[启动] 没有检测到 OPENAI_API_KEY，使用 MockModel（回复是预设的）")
        return MockModel()
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=API_KEY, base_url=BASE_URL or None),
        model=MODEL_NAME,
        context_size=32_768,
    )


def build_agent() -> Agent:
    agent = Agent(
        name="Buddy",
        system_prompt=(
            "你叫 Buddy，一个文件助手。回答简洁。"
            "需要写文件时直接调用 Write 工具。"
            # Write 只接受绝对路径，写成相对路径会直接报错，
            # 所以这里把沙箱的绝对路径原样告诉模型。
            f"所有文件一律写到 {SANDBOX} 目录下。"
        ),
        model=build_model(),
        toolkit=Toolkit(tools=[Write()]),
    )
    agent.state.permission_context.mode = PermissionMode.DEFAULT
    return agent


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str
    text: str


class ConfirmRequest(BaseModel):
    session_id: str
    confirmed: bool


app = FastAPI(title="mini-workbuddy")


# ---------------------------------------------------------------------------
# 静态页
# ---------------------------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@app.post("/api/sessions")
async def create_session() -> dict[str, Any]:
    session = registry.create(build_agent)
    return {"session_id": session.session_id}


@app.get("/api/sessions")
async def list_sessions() -> dict[str, Any]:
    return {"items": registry.list()}


def _get(session_id: str) -> Session:
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


# ---------------------------------------------------------------------------
# 对话与事件流
# ---------------------------------------------------------------------------
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

    async def gen() -> AsyncGenerator[str, None]:
        # 先发一个注释帧，让浏览器立刻建立连接
        yield ": connected\n\n"

        agen = session.subscribe(from_seq=last_event_id)
        while True:
            try:
                frame = await asyncio.wait_for(agen.__anext__(), timeout=HEARTBEAT)
            except asyncio.TimeoutError:
                # 心跳：中间有代理或负载均衡时，长时间无数据会被掐断
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                return
            yield frame

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",     # 关掉 Nginx 缓冲，否则会攒着不发
        },
    )


@app.post("/api/confirm")
async def confirm(body: ConfirmRequest) -> dict[str, Any]:
    session = _get(body.session_id)
    pending = session.confirm_pending
    if pending is None:
        raise HTTPException(status_code=409, detail="当前没有待确认的调用")

    results = [
        ConfirmResult(confirmed=body.confirmed, tool_call=call)
        for call in pending.tool_calls
    ]
    session.confirm_pending = None
    # 续跑：把确认结果作为输入再喂进 reply_stream
    await session.start_resume(
        UserConfirmResultEvent(reply_id=pending.reply_id, confirm_results=results),
    )
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8848)
