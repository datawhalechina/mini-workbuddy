"""第 13 章复用第 11 章的 SSE 传输测试。"""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ch11"))
sys.path.insert(0, str(HERE))

import test_stream as common  # noqa: E402


class InteractiveStreamTests(common.StreamTests):
    chapter = common.load_module("ch13_stream_test_app", HERE / "server.py")
