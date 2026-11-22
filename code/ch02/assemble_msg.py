"""第 2 章附件：手工拼一条 assistant 消息，看清 content 里的四个块。

运行：python assemble_msg.py

对应文档 2.2.1 节。把「看看 config.yaml 里数据库地址是多少」这个场景
原样写成一条 Msg：思考 -> 工具调用 -> 工具结果 -> 文本回答，
顺序就是它们真实发生的顺序。
"""

from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    Usage,
)
from agentscope.types import ReplyFinishedReason

# 值太长时截断，保证一个块只占一行，方便对照文档里的输出
WIDTH = 66


def shorten(value: object) -> str:
    """把块压成一行短字符串。"""
    text = repr(value).replace("\n", " ")
    return text if len(text) <= WIDTH else text[: WIDTH - 3] + "..."


def main() -> None:
    msg = AssistantMsg(
        name="Buddy",
        content=[
            # ① 内部推理：先读文件
            ThinkingBlock(thinking="用户问的是文件内容，得先读一下 config.yaml"),
            # ② 决定调用工具：id 是调用与结果的配对依据
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

    # 一行看全貌：四个块都在 content 里，按顺序排列
    print(msg)
    print()

    # 逐个块看：类型 + 内容
    print("逐个块看：")
    for idx, block in enumerate(msg.content):
        print(f"[{idx}] {block.type:<12} {shorten(block)}")
    print()

    # 对比：压成字符串后，前三个块全部消失
    print(f"get_text_content() -> {msg.get_text_content()!r}")


if __name__ == "__main__":
    main()
