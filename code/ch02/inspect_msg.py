"""第 2 章附件：不用 API key，直接看清一个 Msg 里到底装了什么。

运行：python inspect_msg.py

四件事：
  1. 一条用户消息的 11 个字段分别是什么
  2. name 和 role 的区别：同一条消息换掉 name，模型看到的东西就变了
  3. content 为什么是列表：手工拼一条带工具调用的 assistant 消息
  4. role 的块类型约束在构造阶段就会被检查
"""

import asyncio

from pydantic import ValidationError

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    Usage,
    UserMsg,
)
from agentscope.types import ReplyFinishedReason

# 值太长时截断，保证一行一个字段，方便对照文档里的表格
WIDTH = 66


def shorten(value: object) -> str:
    """把字段值压成一行短字符串。"""
    text = repr(value).replace("\n", " ")
    return text if len(text) <= WIDTH else text[: WIDTH - 3] + "..."


def dump(msg: Msg, title: str) -> None:
    """逐字段打印一条消息：字段名 / 类型 / 值。"""
    print("=" * 78)
    print(title)
    print("=" * 78)
    for name in Msg.model_fields:  # 字段清单直接取自 Msg 的定义，不会漏
        value = getattr(msg, name)
        print(f"{name:<17} {type(value).__name__:<18} {shorten(value)}")
    print()


def demo_user_msg() -> Msg:
    """1. 一次用户输入：最朴素的一条 Msg。"""
    msg = UserMsg(name="user", content="用一句话说明什么是异步编程")
    dump(msg, "一、用户消息：只有 3 个字段有值，其余是自动填充或仍为 None")
    print("注意三件事：")
    print("  · id / created_at 是框架自动生成的，不用你填")
    print("  · finished_at 在这里就被填上了（用户消息不需要「生成过程」）")
    print("  · usage / finished_reason / structured_output / error 都还是 None\n")
    return msg


def demo_name_vs_role() -> None:
    """2. name 和 role：一个是谁说的，一个是怎么处理。"""
    print("=" * 78)
    print("二、name 和 role 都是 'user'，但管的是两件事")
    print("=" * 78)

    try:
        UserMsg(content="你好")  # 少填 name
    except TypeError as exc:
        print(f"name 没有默认值，必须自己填：{exc}")
    print()

    formatter = OpenAIChatFormatter()
    for name in ("user", "张三"):
        msg = UserMsg(name=name, content="我读哪张表？")
        formatted = asyncio.run(formatter.format([msg]))
        print(f"name={name!r:<8} role={msg.role!r:<8} -> 发给模型：{formatted}")
    print()
    print("role 恒为 'user'（UserMsg 锁死的），只有 name 变了，模型看到的名字就变了。")
    print("role 决定走哪条格式化路径，name 只是内容里的一个标签。\n")


def demo_assistant_msg() -> Msg:
    """3. 一次完整回复：content 里有四个块，按发生顺序排列。"""
    msg = AssistantMsg(
        name="Buddy",
        content=[
            # ① 内部推理
            ThinkingBlock(thinking="用户问的是文件内容，得先读一下 config.yaml"),
            # ② 决定调用工具
            ToolCallBlock(
                id="call_1",
                name="Read",
                input='{"file_path": "config.yaml"}',
                state="finished",
            ),
            # ③ 工具返回了什么（这一步最容易被字符串化丢掉）
            ToolResultBlock(
                id="call_1",
                name="Read",
                output="database:\n  host: 127.0.0.1\n  port: 5432",
                state="success",
            ),
            # ④ 基于结果作答
            TextBlock(text="数据库地址是 127.0.0.1:5432。"),
        ],
        usage=Usage(input_tokens=186, output_tokens=54),
        finished_reason=ReplyFinishedReason.COMPLETED,
    )
    dump(msg, "三、assistant 消息：content 是 4 个块的列表，而不是一段字符串")

    print("逐个块看：")
    for idx, block in enumerate(msg.content):
        print(f"  [{idx}] {block.type:<12} {shorten(block)}")
    print()

    print("如果用 get_text_content() 压成字符串，只剩：")
    print(f"  {msg.get_text_content()!r}")
    print("  → 思考、工具名、工具返回的文件内容全部丢失。")
    print("    下一轮模型会「记得读过文件，却不记得读到了什么」。\n")
    return msg


def demo_role_rule() -> None:
    """4. role 的约束在构造阶段就会检查。"""
    print("=" * 78)
    print("四、role 约束：user 角色不能携带工具调用块")
    print("=" * 78)
    try:
        UserMsg(
            name="user",
            content=[ToolCallBlock(id="c1", name="Read", input="{}")],
        )
    except ValidationError as exc:
        print(f"构造失败：{exc.errors()[0]['msg']}")
        print("→ 错误在本地就被拦下，不用等到请求 API 收到 400。")
    else:
        print("（没有报错：说明当前版本放宽了这条约束）")


def main() -> None:
    demo_user_msg()
    demo_name_vs_role()
    demo_assistant_msg()
    demo_role_rule()


if __name__ == "__main__":
    main()
