"""第 12 章的启动脚本。

    MOCK=1 python main.py          # 假模型，能完整走一遍确认与提问
    python main.py                 # 配好 .env 就是真模型

应用本身在 server.py，这个文件只负责把它跑起来。
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from server import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
