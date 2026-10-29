"""第 10 章任务分级回归：python -m unittest discover -s code/ch10。"""

import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse, ChatUsage, FinishedReason

from offline_demo import ScriptedModel


class RoutingModel(ScriptedModel):
    """若分类阶段仍提供 Write，就先写一个临时标记，再返回 high。"""

    def __init__(self, marker: pathlib.Path) -> None:
        super().__init__()
        self.marker = marker
        self.wrote = False

    async def __call__(self, messages, tools=None, **kwargs):
        self.calls += 1
        names = {tool["function"]["name"] for tool in tools or []}
        if "GenerateStructuredOutput" not in names:
            block = TextBlock(text="工作区已检查。")
        elif "Write" in names and not self.wrote:
            self.wrote = True
            block = ToolCallBlock(
                id="write-marker", name="Write",
                input=json.dumps({"file_path": str(self.marker), "content": "ran before classification"}),
            )
        else:
            block = ToolCallBlock(
                id="routing-result", name="GenerateStructuredOutput",
                input=json.dumps({
                    "level": "complex", "needs_tools": True, "risk": "high",
                    "reason": "涉及删除或外发", "steps": ["转人工审批"],
                }),
            )
        return ChatResponse(
            content=[block], is_last=True, id=f"response-{self.calls}",
            usage=ChatUsage(input_tokens=10, output_tokens=2, time=0),
            finished_reason=FinishedReason.COMPLETED,
        )


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "ch10_app", pathlib.Path(__file__).with_name("main.py"),
        )
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.marker = pathlib.Path(self.temp.name) / "marker.txt"
        self.model = RoutingModel(self.marker)
        self.audit = self.app.AuditMiddleware()
        self.budget = self.app.BudgetMiddleware()

    async def run_demo(self) -> str:
        output = io.StringIO()
        with (
            patch.object(self.app, "WORKDIR", self.temp.name),
            patch.object(self.app, "build_model", return_value=self.model),
            patch.object(self.app, "AuditMiddleware", return_value=self.audit),
            patch.object(self.app, "BudgetMiddleware", return_value=self.budget),
            contextlib.redirect_stdout(output),
        ):
            await self.app.main()
        return output.getvalue()

    async def test_classification_cannot_execute_workspace_tools(self) -> None:
        output = await self.run_demo()
        self.assertIn("[拦截] 风险等级 high", output)
        self.assertFalse(self.marker.exists(), "工作区工具在风险判定前已执行")

    async def test_classification_usage_is_included_in_the_same_budget(self) -> None:
        await self.run_demo()
        self.assertEqual(self.budget.used, 24)
        calls = [r for r in self.audit.records if r["kind"] == "model_call"]
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
