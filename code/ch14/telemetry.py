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
from typing import Any, Iterator

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

    def read(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fp:
            lines = fp.readlines()[-limit:]
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

    def iter_rows(self) -> Iterator[dict[str, Any]]:
        """逐行流式读，不把整份日志读进内存。

        `read(limit)` 的窗口是给"看最近 N 条"用的；汇总要走这里 ——
        走 read() 就会把历史账目截断（见下面 summary 的注释）。
        """
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue          # 写到一半断电会留下半行，跳过即可

    def summary(self, session_id: str | None = None) -> dict[str, Any]:
        """按会话汇总。不传 session_id 就是全局。

        这里**不能**用 `self.read()`：它的默认窗口是最近 500 行，于是账目
        从一开始就最多只数 500 条，两个会话交替时先记的那个会整段归零。
        改成流式全量聚合——读多少算多少，内存不随日志增长。
        """
        model_calls = 0
        tool_calls = 0
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        elapsed_ms = 0
        cost = 0.0
        slowest: dict[str, Any] | None = None
        failed_tools: list[dict[str, Any]] = []

        for r in self.iter_rows():
            if session_id is not None and r.get("session_id") != session_id:
                continue
            kind = r.get("kind")
            if kind == "model_call":
                model_calls += 1
                input_tokens += r.get("input_tokens", 0)
                output_tokens += r.get("output_tokens", 0)
                cached_tokens += r.get("cached_tokens", 0)
                cost += r.get("cost", 0.0)
                ms = r.get("elapsed_ms", 0)
                elapsed_ms += ms
                if slowest is None or ms > slowest.get("elapsed_ms", 0):
                    slowest = r
            elif kind == "tool":
                tool_calls += 1
                if r.get("state") == "error":
                    failed_tools.append(
                        {"tool": r.get("tool"), "state": r.get("state")}
                    )

        return {
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cost": round(cost, 6),
            "elapsed_ms": elapsed_ms,
            "slowest": slowest,
            "failed_tools": failed_tools,
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
