"""第 12 章：同一套后端，换一张脸。

后端逻辑一行没改——直接复用第 11 章的 FastAPI 应用，
只把静态目录从 `ch11/static` 换成 `ch12/` 整个目录。

启动：
    python main.py
    # 没有可用 key 时先跑假事件流：
    MOCK=1 python main.py
    然后打开 http://127.0.0.1:8000/

想直接看界面长什么样、又不想起服务：
    用浏览器打开 mock.html（?scene=stream / tool / error / task）
"""

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

# 把第 11 章的目录放进搜索路径，复用它的 runtime.py 与 main.py
sys.path.insert(0, str(HERE.parent / "ch11"))

# 按路径加载并使用不同的模块名，避免 uvicorn main:app 导入本章时
# import main 又拿到尚未初始化完成的自己。
spec = importlib.util.spec_from_file_location("ch11_app", HERE.parent / "ch11" / "main.py")
ch11 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ch11)

from fastapi.staticfiles import StaticFiles  # noqa: E402

app = ch11.app

# 第 11 章在 "/" 上挂了它自己的验证页；本章要把根路径让给真正的界面，
# 所以先摘掉那条路由，再把整个目录挂到 "/" 上。
app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/"]
app.mount("/", StaticFiles(directory=str(HERE), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(__import__("os").getenv("PORT", "8000")))
