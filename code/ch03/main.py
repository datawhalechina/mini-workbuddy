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
from agentscope.message import TextBlock, ToolResultState, UserMsg
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import (
    Bash,
    FunctionTool,
    Read,
    Toolkit,
    ToolChunk,
    ToolMiddlewareBase,
    Write,
)

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


# ---------------------------------------------------------------------------
# 工作区边界：给工具一个"只能待在这里"的范围
#
# 别指望提示词。Read / Write 只收绝对路径，模型拼出来的绝对路径从哪来？它
# 只知道"这台机器上有什么"，不知道"我该待在哪"——这个维度它压根没有。
# 框架自己的工具描述甚至写着"You can access any file directly"。
#
# 所以边界只能在代码里夹一层：路径先收敛，越界就拒。
# ---------------------------------------------------------------------------

WORKDIR = Path(__file__).resolve().parent


def safe_path(path: str) -> Path:
    """把模型给的路径收敛到 WORKDIR 内部，越界抛异常。

    expanduser 先展开 ~；resolve 再展开 .. 和软链接。
    注意 path 是绝对路径时，`WORKDIR / path` 的结果直接就是 path——
    所以下面那次 is_relative_to 校验不是多余，是唯一的拦网点。
    """
    target = (WORKDIR / Path(path).expanduser()).resolve()
    if not target.is_relative_to(WORKDIR):
        raise PermissionError(f"{path} 不在工作区内（{WORKDIR}）")
    return target


class WorkspaceGuard(ToolMiddlewareBase):
    """拦在工具前面：参数里带路径的，先收敛再执行。

    只管得住读写这类"给我一个路径"的工具。Bash 收到的是一个字符串，
    `cd / && rm -rf ~` 一句话就出去了，这一层拦不住——那种只能换执行环境
    （容器 / bubblewrap），第 7 章讲。
    """

    async def on_tool_call(self, tool, input_kwargs, next_handler):
        for key in ("file_path", "path"):
            value = input_kwargs.get(key)
            if isinstance(value, str):
                try:
                    input_kwargs[key] = str(safe_path(value))
                except PermissionError as exc:
                    # 拒绝也要变成一条正常结果回给模型，别让它以为工具炸了
                    yield ToolChunk(
                        content=[TextBlock(text=f"Error: {exc}")],
                        state=ToolResultState.ERROR,
                        is_last=True,
                    )
                    return
        async for chunk in next_handler(**input_kwargs):
            yield chunk


def build_toolkit() -> Toolkit:
    """把工具装配成一个 Toolkit。"""
    return Toolkit(
        tools=[
            FunctionTool(get_current_time),
            FunctionTool(count_text),
            Read(middlewares=[WorkspaceGuard()]),   # 读文件
            Write(middlewares=[WorkspaceGuard()]),  # 写文件
            Bash(),  # 执行 shell 命令（这一层管不住，见上面的类注释）
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
    # 把进程工作目录切到本章脚本所在目录，这样 `python fizz.py` 找得到文件。
    # 注意 chdir 只改了 Bash 的相对路径起点，它拦不住 Read/Write 的绝对路径——
    # 真正的边界是上面那个 WorkspaceGuard。
    os.chdir(WORKDIR)

    toolkit = build_toolkit()
    await print_tool_schemas(toolkit)

    agent = Agent(
        name="Buddy",
        system_prompt=(
            "你叫 Buddy，是一个住在命令行里的工作助手。"
            "需要用工具获取信息时必须调用工具，不要凭记忆猜测。短答即可。"
            # 提示词只负责"告诉它家在哪儿"，强制由 WorkspaceGuard 完成。
            # 只告知不强制，它照样可能越界；只强制不告知，它只会一遍遍撞墙。
            f"你的工作区是 {WORKDIR}，所有文件操作都只能在这个目录里。"
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
