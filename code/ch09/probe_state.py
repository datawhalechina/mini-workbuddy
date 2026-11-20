"""第 9 章：开工前先跑一遍，看清会话是怎么活下来的。

全程只碰 AgentState，不造 Agent，也不调模型，所以不需要 API key。
四段输出：两个 state 各记各的、磁盘上多出两个文件、删掉对象还能读回、三个字段的去留。

    cd code/ch09 && python probe_state.py
"""

import asyncio
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agentscope.message import UserMsg                        # noqa: E402
from agentscope.state import AgentState                       # noqa: E402
from agentscope.tool import TaskCreate                        # noqa: E402

from store import JsonStateStore                              # noqa: E402

USER, AGENT = "mu", "buddy"


async def main() -> None:
    root = HERE / "sandbox" / "_probe_sessions"
    shutil.rmtree(root, ignore_errors=True)
    store = JsonStateStore(str(root))

    # ---------- 1. 记忆跟着 state 走，不跟着程序走 ----------
    print("--- 1. 同一个进程里两个 state，各记各的")
    a, b = AgentState(), AgentState()
    a.context.append(UserMsg(name="user", content="我的周报固定分三段：进度、问题、下周计划"))
    print(f"  state a：{len(a.context)} 条消息　id {a.session_id[:8]}…")
    print(f"  state b：{len(b.context)} 条消息　id {b.session_id[:8]}…")
    print(f"  往 a 塞了一句话，b 里照样是 {len(b.context)} 条")

    # ---------- 2. 三个键，三层目录 ----------
    print()
    print("--- 2. 存下来之后，磁盘上是这个形状")
    await TaskCreate().call(_agent_state=a, subject="写进度", description="本周完成项")
    await TaskCreate().call(_agent_state=a, subject="写问题", description="阻塞项")
    store.save(USER, AGENT, a)
    for p in sorted(root.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(root).as_posix()}　{p.stat().st_size} B")

    # ---------- 3. 删掉内存对象，再从文件读回来 ----------
    print()
    print("--- 3. 把内存里的 state 删掉，再从文件读回来")
    sid = a.session_id
    del a
    print("  del 掉了，进程里已经没有这个对象")
    back = store.load(USER, AGENT, sid)
    print(f"  读回一条：{back.context[0].get_text_content()}")
    print(f"  任务：{[t.subject for t in back.tasks_context.tasks]}")
    print(f"  消息 {len(back.context)} 条　session_id 是不是同一个：{back.session_id == sid}")

    # ---------- 4. 恢复时，哪些该清，哪些该留 ----------
    print()
    print("--- 4. 存进去之前的残留，读回来变成什么样")
    back.reply_context.structured_schema = {"type": "object", "properties": {}}
    back.reply_context.cur_iter = 3
    back.tool_context.activated_groups = ["reporting"]
    store.save(USER, AGENT, back)
    print("  存盘时：structured_schema 有值　cur_iter=3　activated_groups=['reporting']")
    again = store.load(USER, AGENT, sid)
    print(
        f"  读回后：structured_schema={again.reply_context.structured_schema}　"
        f"cur_iter={again.reply_context.cur_iter}　"
        f"activated_groups={again.tool_context.activated_groups}",
    )
    print("  前两个被 load() 清掉了，工具状态原样留着")

    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
