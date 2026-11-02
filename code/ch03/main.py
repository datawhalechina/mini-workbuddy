"""第 3 章：给它一双手。

比上一章多了什么：
  1. Toolkit —— 工具注册表
  2. 两个自定义工具（用 FunctionTool 把普通函数变成工具）
  3. 三个内置工具（Read / Write / Bash）
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.message import UserMsg
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import Bash, FunctionTool, Read, Toolkit, Write

load_dotenv(override=True)

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def build_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=API_KEY, base_url=BASE_URL or None),
        model=MODEL_NAME,
    )


# ---------------------------------------------------------------------------
# 自定义工具：就是普通函数 + 类型注解 + docstring
# ---------------------------------------------------------------------------


def get_current_time() -> str:
    """获取当前的日期和时间。

    当用户询问"现在几点""今天几号""星期几"这类问题时使用本工具。
    你无法感知真实时间，遇到时间问题必须调用本工具，不要凭记忆回答。
    """
    now = datetime.now()
    week = "一二三四五六日"[now.weekday()]
    return now.strftime("%Y-%m-%d %H:%M:%S") + f"，星期{week}"


def count_text(text: str) -> str:
    """统计一段文本的字符数、中文字数与行数。

    Args:
        text: 需要统计的文本内容。
    """
    if not text:
        return "文本为空。"
    chinese = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    lines = text.count("\n") + 1
    return f"总字符数 {len(text)}，其中中文 {chinese} 个字，共 {lines} 行。"


def build_toolkit() -> Toolkit:
    """把工具装配成一个 Toolkit。"""
    return Toolkit(
        tools=[
            FunctionTool(get_current_time),
            FunctionTool(count_text),
            Read(),  # 读文件
            Write(),  # 写文件
            Bash(),  # 执行 shell 命令
        ],
    )


async def print_tool_schemas(toolkit: Toolkit) -> None:
    """打印工具清单——这就是模型在每次推理前看到的东西。"""
    print("=" * 66)
    print("模型能看到的工具清单")
    print("=" * 66)
    for schema in await toolkit.get_tool_schemas():
        fn = schema.get("function", schema)
        params = fn.get("parameters", {}).get("properties", {})
        desc = (fn.get("description") or "").splitlines()[0]
        print(f"· {fn.get('name')}")
        print(f"  描述：{desc}")
        print(f"  参数：{list(params.keys()) or '无'}")
    print()


async def main() -> None:
    # 把工作目录切到本章脚本所在目录，这样模型写出的 fizz.py 会落在 code/ch03/，
    # 后面 `python fizz.py` 也能直接找到它。
    os.chdir(Path(__file__).resolve().parent)

    toolkit = build_toolkit()
    await print_tool_schemas(toolkit)

    agent = Agent(
        name="Buddy",
        system_prompt=(
            "你叫 Buddy，是一个住在命令行里的工作助手。"
            "需要用工具获取信息时必须调用工具，不要凭记忆猜测。短答即可。"
        ),
        model=build_model(),
        toolkit=toolkit,
    )

    # 这一章的脚本没有确认界面。Bash 这类工具默认会"停下来等人点头"，
    # 在这个没有界面的场景里就会卡住，后面再发消息还会报错。
    # 先整体放行，把注意力放在"工具能不能用起来"；第 7 章专门讲怎么管住它。
    agent.state.permission_context.mode = PermissionMode.BYPASS

    # 让 shell 里的 python 一定是当前这个解释器。
    # 别把绝对路径拼进命令里：Windows 路径带反斜杠，进 bash 会被当转义符；
    # 加了引号又会被当成一个整体的命令名，照样找不到。
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")

    questions = [
        "现在几点？今天星期几？",
        "帮我数一下这句话有多少字：今天天气不错，适合写代码。",
        # 第三问故意让它"写 + 跑"：一次触发 Write、Bash 两个工具。
        "把 1 到 20 的 FizzBuzz 写成 fizz.py，"
        "然后用 python fizz.py 跑一遍，把标准输出原样贴给我。",
    ]

    for question in questions:
        print(f">>> 你：{question}")
        reply = await agent.reply(UserMsg(name="user", content=question))
        print(f"<<< Buddy：{reply.get_text_content()}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
