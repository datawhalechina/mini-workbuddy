"""第 14 章：在第 13 章的产品上加可观测。

比上一章多了什么：
  1. 记账：每次模型调用、每次工具调用落一行 JSONL
  2. /api/usage 与 /api/usage/log：汇总与明细
  3. 前端右栏多一块"成本"和"调用明细"

做法上只换了两处，路由一行没改：
  · 会话类换成 UsageSession，它在发事件的同一处顺手记账
  · 注册表换成 UsageRegistry，让新会话都用上面这个类

启动：
    MOCK=1 python main.py
    PRICE_IN=2 PRICE_OUT=8 python main.py   # 改成你自己模型的价格（元/百万 token）
"""

import os
import pathlib
import sys
import uuid
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ch13"))     # 第 13 章的应用
sys.path.insert(0, str(HERE.parent / "ch11"))     # 第 11 章的运行时

from fastapi import Query                                # noqa: E402
from fastapi.staticfiles import StaticFiles              # noqa: E402
from starlette.routing import Mount                      # noqa: E402

import server as ch13                                    # noqa: E402  第 13 章的 app

from telemetry import PRICE_IN, PRICE_OUT, Recorder, UsageLog   # noqa: E402

LOG_PATH = HERE / "logs" / "usage.jsonl"
usage_log = UsageLog(LOG_PATH)


# ---------------------------------------------------------------------------
# 一、会记账的会话
# ---------------------------------------------------------------------------
class UsageSession(ch13.InteractiveSession):
    """每个会话保存自己的计时状态，每发出一帧，顺手记一笔。

    选 `_emit` 而不是中间件，是因为记账要的信息本来就在事件里：
    前端在 MODEL_CALL_END 里看到的 token，和日志里要记的是同一份数据。
    多一个采信来源，就多一处对不上的可能。
    """

    def __init__(self, session_id, agent) -> None:
        super().__init__(session_id, agent)
        self.recorder = Recorder(usage_log)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        super()._emit(event_type, data)          # 先照常推给前端
        try:
            self.recorder.observe(event_type, data, self.session_id)
        except Exception as error:               # noqa: BLE001
            # 记账失败不该把这一轮对话搞挂
            print(f"[telemetry] 记账失败 {event_type}: {error}")


class UsageRegistry(ch13.InteractiveRegistry):
    def create(self, agent_factory) -> UsageSession:   # type: ignore[override]
        session = UsageSession(uuid.uuid4().hex[:12], agent_factory())
        self.sessions[session.session_id] = session
        return session


# 换掉注册表。第 13 章的路由读的是模块里的这个名字，所以不用动它们。
ch13.registry = UsageRegistry()

app = ch13.app


# ---------------------------------------------------------------------------
# 二、两个查询接口
# ---------------------------------------------------------------------------
# 静态目录是兜底路由，必须排在最后。新增接口要插在它前面，
# 而且这一章的静态目录换成了 ch14/（页面多了一块成本面板）。
# ---------------------------------------------------------------------------
app.router.routes = [r for r in app.router.routes if not isinstance(r, Mount)]


@app.get("/api/usage")
async def usage(session_id: str | None = Query(default=None)) -> dict[str, Any]:
    """一次会话（或全局）的账。"""
    return {
        "summary": usage_log.summary(session_id),
        "price": {"in": PRICE_IN, "out": PRICE_OUT},
    }


@app.get("/api/usage/log")
async def usage_detail(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=30, le=200),
) -> dict[str, Any]:
    """最近若干条调用明细，新的在前。"""
    rows = [
        r for r in usage_log.read(200)
        if session_id is None or r.get("session_id") == session_id
    ]
    return {"items": rows[::-1][:limit]}


app.mount("/", StaticFiles(directory=str(HERE), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    print(f"[telemetry] 记录写到 {LOG_PATH}")
    print(f"[telemetry] 价格 输入 {PRICE_IN} 元/百万，输出 {PRICE_OUT} 元/百万")
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
