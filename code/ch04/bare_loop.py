"""第 4 章。

这个文件的唯一目的是让你看清循环的本质。
写完之后再看 main.py，你就知道框架到底替你做了什么。

注意：这里直接用 openai SDK，没有 import agentscope。
"""

import asyncio
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(override=True)

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL or None)


# ---------------------------------------------------------------------------
# 一、工具的真实实现
# ---------------------------------------------------------------------------


def get_current_time() -> str:
    now = datetime.now()
    week = "一二三四五六日"[now.weekday()]
    return now.strftime("%Y-%m-%d %H:%M:%S") + f"，星期{week}"


def count_text(text: str) -> str:
    chinese = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return f"总字符数 {len(text)}，其中中文 {chinese} 个字。"


# 名字 -> 函数，后面按模型给的名字查表
REGISTRY = {"get_current_time": get_current_time, "count_text": count_text}


# ---------------------------------------------------------------------------
# 二、手写的工具说明（框架会自动生成，这里只能自己写）
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的日期和时间。用户问时间、日期、星期时必须调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_text",
            "description": "统计一段文本的字符数与中文字数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "需要统计的文本"},
                },
                "required": ["text"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 三、循环本体
# ---------------------------------------------------------------------------


async def bare_loop(question: str, max_iters: int = 5) -> str:
    messages = [
        {"role": "system", "content": "你叫 Buddy，需要用工具获取信息时必须调用工具。"},
        {"role": "user", "content": question},
    ]

    for i in range(1, max_iters + 1):
        resp = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOLS,
        )
        choice = resp.choices[0].message
        print(f"  [第 {i} 轮] 模型返回 tool_calls={len(choice.tool_calls or [])}")

        # 没有工具调用，说明它准备直接回答，循环结束
        if not choice.tool_calls:
            return choice.content or ""

        # 有工具调用：把这次 assistant 消息原样存回历史
        messages.append(
            {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [tc.model_dump() for tc in choice.tool_calls],
            },
        )

        # 逐个执行，把结果作为 tool 消息追加
        for tc in choice.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            print(f"           → 调用 {name}({args})")
            try:
                result = REGISTRY[name](**args)
            except Exception as exc:  # 工具炸了不能把整个程序带崩
                result = f"工具执行失败：{exc}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                },
            )
        # 回到循环开头，再问一次模型

    return "（达到最大轮数，循环终止）"


async def main() -> None:
    question = "现在几点？顺便数一下这句话有多少字：手写循环其实不复杂。"
    print(f">>> {question}\n")
    answer = await bare_loop(question)
    print(f"\n<<< {answer}")


if __name__ == "__main__":
    asyncio.run(main())
