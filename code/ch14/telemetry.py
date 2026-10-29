"""第 14 章：把"花了多少、慢在哪"记下来。

三个东西：
  UsageLog   一个只追加的 JSONL 文件，一行一次调用
  Recorder   把事件流翻译成一行行账目
  price      元 / 百万 token 的单价，走环境变量

为什么不用中间件记 token：`on_model_call` 交给你的只是流式响应的外壳，
真实的 usage 挂在最后一个 chunk 上。要读它就得把流走完再包装回去
（第 10 章的 tap_usage 就是这么做的），代价是每次调用都得在中间件里
多包一层生成器。而事件流里的 MODEL_CALL_END 本来就带着完整的
input_tokens / output_tokens，不用再包一层——**界面上能看到的，
日志里就能记**。醒一次、记一笔，比两套管线各记一遍可靠。

（第 10 章的 tap_usage 和这里的 Recorder 不冲突：一个给中间件用，
一个给进程外的服务用。真正要避免的是同一份账两处各写一半。）

为什么用 JSONL 而不是数据库：一行一条记录，追加不会破坏已有内容，
`tail -5` 就能看，出事能直接 grep。量级上来了再换 SQLite，接口形状不用改。
"""

import json
import os
import time
from pathlib import Path
from typing import Any

# 单价：元 / 百万 token。不同模型差很多，走环境变量，别写死。
PRICE_IN = float(os.getenv("PRICE_IN", "2.0"))
PRICE_OUT = float(os.getenv("PRICE_OUT", "8.0"))


def cost_of(input_tokens: int, output_tokens: int) -> float:
    """按百万 token 计价。四舍五入到 6 位，够看，也不会累加出浮点噪音。"""
    return round(
        input_tokens / 1_000_000 * PRICE_IN
        + output_tokens / 1_000_000 * PRICE_OUT,
        6,
    )


class UsageLog:
    """只追加的记录文件。

    并发写这里没有加锁：单进程单事件循环，`open(..., "a")` 写一行是原子的。
    多 worker 部署要换成带锁的写法，或者直接换 SQLite / Redis。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        # 每次写之前确认目录还在。日志轮转、清理脚本、甚至开发时手抖
        # 删掉 logs/ 都会让它消失，而服务是长期运行的，__init__ 只跑一次。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read(self, limit: int | None = 500) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fp:
            lines = fp.readlines()
        if limit is not None:
            lines = lines[-limit:]
        rows = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # 写到一半断电会留下半行，跳过即可
        return rows

    def summary(self, session_id: str | None = None) -> dict[str, Any]:
        """按会话汇总。不传 session_id 就是全局。"""
        rows = [
            r for r in self.read(limit=None)
            if session_id is None or r.get("session_id") == session_id
        ]
        models = [r for r in rows if r.get("kind") == "model_call"]
        tools = [r for r in rows if r.get("kind") == "tool"]

        return {
            "model_calls": len(models),
            "tool_calls": len(tools),
            "input_tokens": sum(r.get("input_tokens", 0) for r in models),
            "output_tokens": sum(r.get("output_tokens", 0) for r in models),
            "cached_tokens": sum(r.get("cached_tokens", 0) for r in models),
            "cost": round(sum(r.get("cost", 0.0) for r in models), 6),
            "elapsed_ms": sum(r.get("elapsed_ms", 0) for r in models),
            "slowest": max(models, key=lambda r: r.get("elapsed_ms", 0), default=None),
            "failed_tools": [
                {"tool": r.get("tool"), "state": r.get("state")}
                for r in tools if r.get("state") == "error"
            ],
        }


class Recorder:
    """把事件流翻译成账目。

    它只盯两对事件的"开始"和"结束"：
        MODEL_CALL_START → MODEL_CALL_END     拿到 token 用量与耗时
        TOOL_CALL_START  → TOOL_RESULT_END    拿到工具耗时与成败
    """

    def __init__(self, log: UsageLog) -> None:
        self.log = log
        self._model_t0: float | None = None
        self._model_name: str = "unknown"
        self._tool_t0: dict[str, float] = {}
        self._tool_name: dict[str, str] = {}

    def observe(
        self,
        event_type: str,
        data: dict[str, Any],
        session_id: str,
    ) -> None:
        now = float(data.get("ts") or time.time())
        base = {"ts": round(now, 3), "session_id": session_id}

        if event_type == "MODEL_CALL_START":
            self._model_t0 = now
            # 模型名只在 START 里有，END 里没有，所以要提前存下来
            self._model_name = data.get("model_name") or self._model_name

        elif event_type == "MODEL_CALL_END":
            in_tok = int(data.get("input_tokens") or 0)
            out_tok = int(data.get("output_tokens") or 0)
            cached = int(data.get("cache_input_tokens") or 0)
            self.log.append({
                **base,
                "kind": "model_call",
                "model": self._model_name,
                "elapsed_ms": round((now - self._model_t0) * 1000) if self._model_t0 else 0,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cached_tokens": cached,
                "cost": cost_of(in_tok, out_tok),
            })

        elif event_type == "TOOL_CALL_START":
            cid = str(data.get("tool_call_id") or "")
            self._tool_t0[cid] = now
            self._tool_name[cid] = data.get("tool_call_name") or "?"

        elif event_type == "TOOL_RESULT_START":
            # 确认后"续跑"的那次调用不会再发 TOOL_CALL_START，
            # 名字只能从 RESULT_START 里补。开始时间也一起补上。
            cid = str(data.get("tool_call_id") or "")
            self._tool_t0.setdefault(cid, now)
            if data.get("tool_call_name"):
                self._tool_name[cid] = data["tool_call_name"]

        elif event_type == "TOOL_RESULT_END":
            cid = str(data.get("tool_call_id") or "")
            t0 = self._tool_t0.pop(cid, None)
            self.log.append({
                **base,
                "kind": "tool",
                "tool": self._tool_name.pop(cid, "?"),
                "elapsed_ms": round((now - t0) * 1000) if t0 else 0,
                "state": data.get("state") or "unknown",
            })
