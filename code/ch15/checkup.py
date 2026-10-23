"""第 15 章：毕业自检。

跑一遍，六项全过就算毕业：

    cd code/ch15 && python checkup.py

它不启动 Web 服务，直接在进程内驱动会话——把每一轮的事件收进列表再断言。
这样自检脚本自己就不会因为端口、前端、浏览器而失败。

六项对应第 15.1 节的六个任务：
    1 对话通路      文本增量与结束原因
    2 工具落地      允许之后文件真的写出来
    3 权限拦截      拒绝之后文件不存在
    4 中断          运行中叫停，收尾是 interrupted
    5 状态往返      AgentState 序列化再读回来，消息数不变
    6 记账          用量被写进 JSONL，且费用不为零

默认走假模型（MOCK=1），不需要 API key。要对着真模型跑：
    OPENAI_API_KEY=... OPENAI_BASE_URL=... OPENAI_MODEL=... python checkup.py --real
真模型下第 2、3、4 项的触发词不可靠，脚本会如实标注"跳过"。
"""

import asyncio
import json
import os
import pathlib
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ch13"))     # 应用装配
sys.path.insert(0, str(HERE.parent / "ch11"))     # 运行时
sys.path.insert(0, str(HERE.parent / "ch14"))     # 记账

REAL = "--real" in sys.argv
if not REAL:
    os.environ["MOCK"] = "1"
os.environ.setdefault("MOCK_DELAY", "0.02")

import server as ch13                                          # noqa: E402
from agentscope.state import AgentState                       # noqa: E402
from agentscope.event import ConfirmResult, UserConfirmResultEvent   # noqa: E402
from agentscope.message import ToolCallBlock                   # noqa: E402

from telemetry import Recorder, UsageLog                       # noqa: E402

# 工具写到第 13 章那个沙箱里（Agent 的提示词里写的就是这个绝对路径），
# 不是当前目录。跟着应用走，别自己另立一个。
TARGET = pathlib.Path(ch13.SANDBOX) / "notes.md"
LOG = HERE / "logs" / "checkup.jsonl"


# ---------------------------------------------------------------------------
# 探针会话：把发出的每一帧留下来
# ---------------------------------------------------------------------------
class Probe(ch13.Session):
    def __init__(self, session_id, agent, recorder=None) -> None:
        super().__init__(session_id, agent)
        self.events: list[tuple[str, dict]] = []
        self.recorder = recorder

    def _emit(self, event_type, data) -> None:
        self.events.append((event_type, dict(data)))
        if self.recorder is not None:
            self.recorder.observe(event_type, data, self.session_id)
        super()._emit(event_type, data)

    def types(self) -> list[str]:
        return [t for t, _ in self.events]

    def first(self, event_type):
        for et, data in self.events:
            if et == event_type:
                return data
        return None

    def last(self, event_type):
        found = None
        for et, data in self.events:
            if et == event_type:
                found = data
        return found


def new_session(recorder=None) -> Probe:
    return Probe(uuid.uuid4().hex[:8], ch13.build_agent(), recorder)


async def wait(session: Probe, timeout: float = 20.0) -> None:
    """等这一轮跑完。停在确认时任务也会正常结束，所以这里不会挂住。"""
    if session.task is not None:
        await asyncio.wait_for(session.task, timeout)


def take_pending(session: Probe):
    """取出待确认的调用，并把标记清掉——否则下一轮收尾会误发 confirm_required。"""
    pending = session.confirm_pending
    session.confirm_pending = None
    return pending


async def answer(session: Probe, pending, confirmed: bool, extra=None) -> None:
    call = pending.tool_calls[0]
    args = json.loads(call.input) if isinstance(call.input, str) else dict(call.input or {})
    if extra:
        args.update(extra)
    await session.start_resume(
        UserConfirmResultEvent(
            reply_id=pending.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=confirmed,
                    tool_call=ToolCallBlock(
                        type="tool_call",
                        id=call.id,
                        name=call.name,
                        # 工具参数在框架里是 JSON 字符串，回填时也必须是字符串
                        input=json.dumps(args, ensure_ascii=False),
                    ),
                ),
            ],
        ),
    )
    await wait(session)


async def drive_to(session: Probe, question: str, want: str, rounds: int = 4):
    """一直跑到出现 `want` 这个工具的确认请求为止。

    真模型不一定按你预想的路径走：让它写文件，它可能先调用 ask_user
    问"写什么内容"。这种追问要直接答掉，继续往下推，而不是判定失败。
    """
    await session.start_reply(question)
    await wait(session)

    for _ in range(rounds):
        pending = take_pending(session)
        if pending is None:
            return None                      # 这一轮自己结束了，没停下来问
        if pending.tool_calls[0].name == want:
            return pending
        await answer(session, pending, confirmed=True,
                     extra={"answer": "随便写就行：一行标题加两条待办。"})
    return None


# ---------------------------------------------------------------------------
# 六项检查
# ---------------------------------------------------------------------------
async def check_dialog() -> str:
    session = new_session()
    await session.start_reply("你好")
    await wait(session)

    assert "TEXT_BLOCK_DELTA" in session.types(), "没有收到文本增量"
    end = session.first("REPLY_END")
    assert end and end["finished_reason"] == "completed", "结束原因不是 completed"
    usage = session.last("MODEL_CALL_END")
    assert usage and usage["input_tokens"] > 0, "MODEL_CALL_END 里没有 token 用量"
    return f"{len(session.events)} 帧，{usage['input_tokens']}/{usage['output_tokens']} tok"


async def check_tool() -> str:
    TARGET.unlink(missing_ok=True)
    session = new_session()
    pending = await drive_to(
        session, "帮我写文件 notes.md，内容写一行标题和两条待办。", "Write",
    )
    assert pending is not None, "没有等到 Write 的确认请求"

    await answer(session, pending, confirmed=True)
    assert TARGET.exists(), f"允许之后文件没有出现：{TARGET}"
    return f"{TARGET} {TARGET.stat().st_size} 字节"


async def check_deny() -> str:
    TARGET.unlink(missing_ok=True)
    session = new_session()
    pending = await drive_to(
        session, "帮我写文件 notes.md，内容写一行标题和两条待办。", "Write",
    )
    assert pending is not None, "没有等到 Write 的确认请求"

    await answer(session, pending, confirmed=False)
    assert not TARGET.exists(), "拒绝了却还是把文件写了出来"
    return "拒绝生效，文件未被创建"


async def check_interrupt() -> str:
    os.environ["MOCK_DELAY"] = "0.4"          # 慢一点，才来得及叫停
    try:
        session = new_session()
        await session.start_reply("你好")
        await asyncio.sleep(0.2)
        assert session.task is not None and not session.task.done(), "任务已经先结束了"
        session.task.cancel()
        try:
            await session.task
        except asyncio.CancelledError:
            pass
    finally:
        os.environ["MOCK_DELAY"] = "0.02"

    end = session.first("REPLY_END")
    assert end and end["finished_reason"] == "interrupted", "中断后没有 interrupted 的 REPLY_END"
    assert len(session.agent.state.context) >= 1, "中断把上下文清空了"
    return f"停在 {len(session.events)} 帧，上下文保留 {len(session.agent.state.context)} 条"


async def check_state() -> str:
    session = new_session()
    await session.start_reply("你好")
    await wait(session)

    before = len(session.agent.state.context)
    blob = session.agent.state.model_dump_json()
    restored = AgentState.model_validate_json(blob)
    assert len(restored.context) == before, "序列化再读回来，消息数变了"
    assert restored.permission_context.mode == session.agent.state.permission_context.mode
    return f"{len(blob)} 字节 / {before} 条消息"


async def check_usage() -> str:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.unlink(missing_ok=True)
    log = UsageLog(LOG)
    recorder = Recorder(log)

    session = new_session(recorder)
    await session.start_reply("你好")
    await wait(session)

    rows = log.read()
    models = [r for r in rows if r.get("kind") == "model_call"]
    assert models, "记账文件里没有 model_call 记录"
    assert models[-1]["cost"] > 0, "费用是 0，单价或 token 有问题"
    summary = log.summary(session.session_id)
    assert summary["input_tokens"] == models[-1]["input_tokens"]
    return f"{len(rows)} 行，{summary['input_tokens']} tok，¥{summary['cost']}"


CHECKS = [
    ("对话通路", check_dialog),
    ("工具落地", check_tool),
    ("权限拦截", check_deny),
    ("中断", check_interrupt),
    ("状态往返", check_state),
    ("记账", check_usage),
]


async def main() -> int:
    print(f"模式：{'真模型' if REAL else '假模型（MOCK=1）'}")
    print(f"工作目录：{pathlib.Path.cwd()}")
    print("-" * 72)

    failed = 0
    for index, (name, fn) in enumerate(CHECKS, start=1):
        try:
            detail = await fn()
            print(f"  {index}/{len(CHECKS)}  {name:<6} ✓  {detail}")
        except AssertionError as error:
            failed += 1
            print(f"  {index}/{len(CHECKS)}  {name:<6} ✗  {error}")
        except Exception as error:                     # noqa: BLE001
            failed += 1
            print(f"  {index}/{len(CHECKS)}  {name:<6} ✗  {type(error).__name__}: {error}")

    print("-" * 72)
    if failed:
        print(f"{failed} 项没过。对着上面那一行去翻对应的章节。")
        return 1
    print("六项全过。接下来看第 15.2 节，选一条升级路线。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
