"""第 11 章的运行时：把 Agent 的事件流变成浏览器能读的 SSE。

三个关键设计：
  1. 一个会话一个后台任务，客户端断开不取消任务
  2. 事件先进环形缓冲区，再推给当前订阅者；重连可以从 Last-Event-ID 续传
  3. 每个事件编成一帧 SSE：event 是类型，data 是完整 JSON
"""

import asyncio
import json
import time
import uuid
from typing import Any, AsyncGenerator

from agentscope.agent import Agent
from agentscope.event import EventType
from agentscope.message import UserMsg

# 只转发这几类事件给前端，避免把内部细节抖出去
FORWARD = {
    EventType.REPLY_START,
    EventType.REPLY_END,
    EventType.MODEL_CALL_START,
    EventType.MODEL_CALL_END,
    EventType.TEXT_BLOCK_START,
    EventType.TEXT_BLOCK_DELTA,
    EventType.TEXT_BLOCK_END,
    EventType.THINKING_BLOCK_START,
    EventType.THINKING_BLOCK_DELTA,
    EventType.THINKING_BLOCK_END,
    EventType.TOOL_CALL_START,
    # 工具参数是流式吐出来的：TOOL_CALL_START 只有工具名，参数要靠 DELTA 逐片拼。
    EventType.TOOL_CALL_DELTA,
    EventType.TOOL_CALL_END,
    EventType.TOOL_RESULT_START,
    # 工具返回的正文也是流式的，不转这个事件前端就只能看到"完成"两个字。
    EventType.TOOL_RESULT_TEXT_DELTA,
    EventType.TOOL_RESULT_END,
    EventType.HINT_BLOCK,
    EventType.REQUIRE_USER_CONFIRM,
    EventType.EXCEED_MAX_ITERS,
}


def sse_frame(seq: int, event_type: str, data: dict[str, Any]) -> str:
    """一帧 SSE。id 用递增序号；原生 EventSource 会带 Last-Event-ID，本项目前端用查询参数续传。"""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"id: {seq}\nevent: {event_type}\ndata: {payload}\n\n"


def event_to_dict(event: Any) -> dict[str, Any]:
    """把事件对象压成前端好用的扁平结构。"""
    data: dict[str, Any] = {
        "type": event.type.value,
        "reply_id": getattr(event, "reply_id", None),
        "block_id": getattr(event, "block_id", None),
        "ts": time.time(),
    }
    for field in (
        "delta", "tool_call_id", "tool_call_name", "state",
        "input_tokens", "output_tokens", "finished_reason", "hint",
        # 模型名与缓存命中量：第 14 章记账要用，界面上也值得显示
        "model_name", "cache_input_tokens",
    ):
        value = getattr(event, field, None)
        if value is not None and not hasattr(value, "value"):
            data[field] = value
        elif value is not None:
            data[field] = value.value

    # 工具调用参数：ToolCallBlock 的 input 是 JSON 字符串
    block = getattr(event, "tool_call", None)
    if block is not None:
        data["tool_call"] = {
            "id": getattr(block, "id", None),
            "name": getattr(block, "name", None),
            "input": getattr(block, "input", None),
        }

    # 确认事件带的是列表：一次可能并行发起多个调用
    blocks = getattr(event, "tool_calls", None)
    if blocks:
        data["tool_calls"] = [
            {
                "id": getattr(b, "id", None),
                "name": getattr(b, "name", None),
                "input": getattr(b, "input", None),
            }
            for b in blocks
        ]
    return data


class Session:
    """一条会话的运行时状态。"""

    def __init__(self, session_id: str, agent: Agent) -> None:
        self.session_id = session_id
        self.agent = agent
        self.seq = 0
        self.buffer: list[str] = []          # 已经编码好的帧，用于重连续传
        # 每个订阅者一个独立队列。共用一个队列会让两个标签页互相抢帧。
        self.subscribers: list[asyncio.Queue[str | None]] = []
        self.task: asyncio.Task | None = None
        self.running = False
        self.confirm_pending: Any = None
        self.created_at = time.time()

    # ---------- 事件广播 ----------

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        self.seq += 1
        frame = sse_frame(self.seq, event_type, data)
        self.buffer.append(frame)
        # 缓冲区只保留最近 2000 帧，够重连用
        if len(self.buffer) > 2000:
            self.buffer = self.buffer[-2000:]
        for queue in list(self.subscribers):
            queue.put_nowait(frame)

    # ---------- 跑一轮回复 ----------

    async def start_reply(self, text: str) -> None:
        if self.running:
            raise RuntimeError("上一条回复还没结束")
        self.running = True
        self.task = asyncio.create_task(self._run(UserMsg(name="user", content=text)))

    async def start_resume(self, inputs: Any) -> None:
        """用确认结果续跑。inputs 是 UserConfirmResultEvent 之类的续跑事件。"""
        if self.running:
            raise RuntimeError("上一条回复还没结束")
        self.running = True
        self.task = asyncio.create_task(self._run(inputs))

    async def _run(self, inputs: Any) -> None:
        try:
            async for event in self.agent.reply_stream(inputs):
                if event.type == EventType.REQUIRE_USER_CONFIRM:
                    self.confirm_pending = event
                if event.type in FORWARD:
                    self._emit(event.type.value, event_to_dict(event))
        except asyncio.CancelledError:
            self._emit("error", {"message": "任务被取消"})
            raise
        except Exception as error:                     # noqa: BLE001
            self._emit("error", {"message": f"{type(error).__name__}: {error}"})
        finally:
            self.running = False
            if self.confirm_pending is not None:
                # 这一轮没有结束，只是停在等人确认。
                # 不能发 done，也不能关订阅者，否则前端会以为可以重新发问了。
                self._emit("confirm_required", {
                    "session_id": self.session_id,
                    "reply_id": self.confirm_pending.reply_id,
                })
            else:
                self._emit("done", {"session_id": self.session_id})
                for queue in list(self.subscribers):
                    queue.put_nowait(None)             # 通知所有订阅者收尾

    # ---------- 订阅 ----------

    async def subscribe(
        self,
        from_seq: int = 0,
    ) -> AsyncGenerator[str, None]:
        """先补发缓冲区里的历史帧，再实时跟随。"""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.subscribers.append(queue)
        try:
            for frame in self.buffer:
                seq = int(frame.split("\n", 1)[0].removeprefix("id: "))
                if seq > from_seq:
                    yield frame

            while True:
                frame = await queue.get()
                if frame is None:
                    return
                yield frame
        finally:
            # 客户端断开时清理，避免队列泄漏
            if queue in self.subscribers:
                self.subscribers.remove(queue)


class Registry:
    """会话注册表。生产环境这里该换成 Redis，接口形状不变。"""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def create(self, agent_factory) -> Session:
        session_id = uuid.uuid4().hex[:12]
        session = Session(session_id, agent_factory())
        self.sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def list(self) -> list[dict[str, Any]]:
        items = [
            {
                "session_id": s.session_id,
                "running": s.running,
                "messages": len(s.agent.state.context),
                "created_at": s.created_at,
            }
            for s in self.sessions.values()
        ]
        return sorted(items, key=lambda x: x["created_at"], reverse=True)
