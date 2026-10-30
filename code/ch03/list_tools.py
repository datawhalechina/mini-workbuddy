"""第 3 章附件：不用 API key，直接看清"模型每次推理前收到的工具清单"。

运行：python list_tools.py

三件事：
  1. 把 5 个工具装配进 Toolkit（2 个自定义 + 3 个内置）
  2. 打印 Toolkit 生成的 JSON Schema —— 这就是模型看到的世界
  3. 打印一份精简摘要，方便一眼数清"模型的选择空间有多大"
"""

import asyncio
from datetime import datetime

from agentscope.tool import Bash, FunctionTool, Read, Toolkit, Write


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
            Read(),  # 内置：读文件
            Write(),  # 内置：写文件
            Bash(),  # 内置：执行 shell 命令
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


async def print_raw_schema(toolkit: Toolkit) -> None:
    """打印第一个工具的原始 JSON，看看框架到底发了什么。"""
    import json

    schemas = await toolkit.get_tool_schemas()
    print("=" * 66)
    print("原始 JSON Schema（只展示第一个工具）")
    print("=" * 66)
    print(json.dumps(schemas[0], ensure_ascii=False, indent=2))
    print()


async def main() -> None:
    toolkit = build_toolkit()
    await print_tool_schemas(toolkit)
    await print_raw_schema(toolkit)

    count = len(await toolkit.get_tool_schemas())
    print(f"模型的选择空间：{count} 个工具。")
    print("工具越多，挑错的概率越高——不是越多越好，而是越清晰越好。")


if __name__ == "__main__":
    asyncio.run(main())
