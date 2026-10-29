"""第 14 章记账回归测试：python -m unittest discover -s code/ch14。"""

import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from telemetry import UsageLog


class UsageLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = UsageLog(pathlib.Path(self.temp.name) / "usage.jsonl")

    def test_summary_includes_records_before_the_recent_page(self) -> None:
        for _ in range(501):
            self.log.append({
                "kind": "model_call", "session_id": "session-a",
                "input_tokens": 10, "output_tokens": 2, "cost": 0.01,
            })

        summary = self.log.summary()
        self.assertEqual(summary["model_calls"], 501)
        self.assertEqual(summary["input_tokens"], 5010)
        self.assertEqual(summary["cost"], 5.01)
        self.assertEqual(len(self.log.read(30)), 30)

    def test_session_summary_is_not_evicted_by_other_sessions(self) -> None:
        self.log.append({
            "kind": "model_call", "session_id": "old",
            "input_tokens": 100, "output_tokens": 20, "cost": 0.25,
        })
        for _ in range(500):
            self.log.append({"kind": "tool", "session_id": "new"})

        summary = self.log.summary("old")
        self.assertEqual(summary["model_calls"], 1)
        self.assertEqual(summary["input_tokens"], 100)
        self.assertEqual(summary["cost"], 0.25)


class UsageSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "ch14_app", pathlib.Path(__file__).with_name("main.py"),
        )
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = self.app.usage_log
        log_patch = patch.object(self.log, "path", pathlib.Path(self.temp.name) / "usage.jsonl")
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.first = self.app.UsageSession("session-a", None)
        self.second = self.app.UsageSession("session-b", None)

    def test_interleaved_model_calls_keep_their_own_model_and_duration(self) -> None:
        self.first._emit("MODEL_CALL_START", {"ts": 100, "model_name": "model-a"})
        self.second._emit("MODEL_CALL_START", {"ts": 102, "model_name": "model-b"})
        self.first._emit("MODEL_CALL_END", {"ts": 105, "input_tokens": 10})
        self.second._emit("MODEL_CALL_END", {"ts": 108, "input_tokens": 20})

        rows = self.log.read()
        self.assertEqual(
            [(r["session_id"], r["model"], r["elapsed_ms"]) for r in rows],
            [("session-a", "model-a", 5000), ("session-b", "model-b", 6000)],
        )

    def test_tool_call_ids_can_repeat_in_different_sessions(self) -> None:
        self.first._emit("TOOL_CALL_START", {
            "ts": 100, "tool_call_id": "call-1", "tool_call_name": "Read",
        })
        self.second._emit("TOOL_CALL_START", {
            "ts": 102, "tool_call_id": "call-1", "tool_call_name": "Write",
        })
        self.first._emit("TOOL_RESULT_END", {
            "ts": 105, "tool_call_id": "call-1", "state": "success",
        })
        self.second._emit("TOOL_RESULT_END", {
            "ts": 108, "tool_call_id": "call-1", "state": "error",
        })

        rows = self.log.read()
        self.assertEqual(
            [(r["session_id"], r["tool"], r["elapsed_ms"]) for r in rows],
            [("session-a", "Read", 5000), ("session-b", "Write", 6000)],
        )


if __name__ == "__main__":
    unittest.main()
