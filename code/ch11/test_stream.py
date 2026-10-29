"""SSE 心跳与连接清理的离线回归测试：python -m unittest discover -s code/ch11。"""

import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest


CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "ch11"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class StreamTests(unittest.IsolatedAsyncioTestCase):
    chapter = load_module("ch11_stream_test_app", CODE / "ch11" / "main.py")

    async def make_stream(self):
        chapter = self.chapter
        chapter.HEARTBEAT = 0.01
        # 只检查事件传输，不启动模型，也不访问外部服务。
        session = chapter.registry.create(lambda: None)
        response = await chapter.stream(session.session_id)
        stream = response.body_iterator
        self.assertEqual(await anext(stream), ": connected\n\n")
        return session, stream

    async def next_frame(self, stream):
        try:
            return await asyncio.wait_for(anext(stream), timeout=1)
        except StopAsyncIteration:
            self.fail("SSE 在心跳后提前结束，后续模型事件无法送达")

    async def test_events_arrive_after_multiple_heartbeats(self):
        session, stream = await self.make_stream()
        try:
            for _ in range(3):
                self.assertEqual(await self.next_frame(stream), ": ping\n\n")
            session._emit("TEXT_BLOCK_DELTA", {"delta": "late output"})
            frame = await self.next_frame(stream)
            self.assertIn("event: TEXT_BLOCK_DELTA\n", frame)
            self.assertIn('"delta": "late output"', frame)
        finally:
            await stream.aclose()

    async def test_disconnect_removes_idle_subscription(self):
        session, stream = await self.make_stream()
        try:
            self.assertEqual(await self.next_frame(stream), ": ping\n\n")
            self.assertEqual(len(session.subscribers), 1)
        finally:
            await stream.aclose()
        self.assertEqual(session.subscribers, [])

    async def test_cancelling_connection_keeps_reply_running(self):
        session, stream = await self.make_stream()
        session.task = asyncio.create_task(asyncio.Event().wait())
        next_frame = asyncio.create_task(anext(stream))
        try:
            # 等订阅者注册完成后模拟 StreamingResponse 的断线取消。
            async with asyncio.timeout(1):
                while not session.subscribers:
                    await asyncio.sleep(0)
            next_frame.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await next_frame
            self.assertEqual(session.subscribers, [])
            self.assertFalse(session.task.done())
        finally:
            session.task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await session.task
            await stream.aclose()


if __name__ == "__main__":
    unittest.main()
