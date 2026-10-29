"""第 12 章既可以直接运行，也应能由 ASGI 服务器导入。"""

from pathlib import Path
import subprocess
import sys
import unittest


class AppImportTests(unittest.TestCase):
    def test_import_main_exposes_the_chapter_app(self):
        result = subprocess.run(
            [sys.executable, "-c", "import main; assert main.app is not None"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
