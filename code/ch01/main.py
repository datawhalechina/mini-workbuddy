"""第 1 章：让 mini-workbuddy 开口说第一句话。

这一章只做一件事：创建一个 Agent，然后和它聊几轮。
比上一章多了什么：没有上一章，这是起点。
"""

# asyncio：标准库的异步框架。Agent 的 reply() 是异步方法，必须放在事件循环里跑
import asyncio
# os：用来读取环境变量（API key、base_url、模型名都放这里）
import os

# load_dotenv：把项目根目录 .env 文件里的键值对加载进 os.environ
from dotenv import load_dotenv

# Agent：框架里的智能体主体，负责维护对话历史、调用模型
from agentscope.agent import Agent
# OpenAICredential：模型的凭证对象（api_key + 可选 base_url）
from agentscope.credential import OpenAICredential
# UserMsg：用户消息的数据结构，用来把一行文本包装成「用户说的话」
from agentscope.message import UserMsg
# OpenAIChatModel：OpenAI 兼容协议的聊天模型实现
from agentscope.model import OpenAIChatModel

# override=True：.env 里的值优先于系统已有环境变量，改配置后立即生效
load_dotenv(override=True)

# 配置：全部从环境变量读，别写死在代码里。换模型只改 .env，代码一行不动。
API_KEY = os.getenv("OPENAI_API_KEY", "")  # 密钥；第二个参数是取不到时的默认值
BASE_URL = os.getenv("OPENAI_BASE_URL", "")  # 留空则走 OpenAI 官方地址
MODEL_NAME = os.getenv("OPENAI_MODEL", "")  # 模型名，必须自己在 .env 里配，没有默认值

PROMPT = "> "  # 输入提示符
# 一组退出指令，大小写不敏感（比较时统一转小写）
EXIT_WORDS = {"/exit", "/quit", "exit", "quit", "q"}


def build_model() -> OpenAIChatModel:
    """造一个模型实例。后面每一章都复用它。"""
    return OpenAIChatModel(
        # 凭证：key 必填；base_url 为空时传 None，让 SDK 用官方默认地址
        credential=OpenAICredential(
            api_key=API_KEY,
            base_url=BASE_URL or None,  # 空字符串是假值，这里顺便转成 None
        ),
        model=MODEL_NAME,  # 指定要调用的模型
    )


async def chat_loop(agent: Agent) -> None:
    """读一行 -> 问一句 -> 打一句，直到你说退出。

    这个 while 循环是本文件唯一"多余"的东西，但它换来两件事：
    1. 你可以连着问，像真的在用一个助手，而不是跑一次脚本看一行输出；
    2. 你会亲眼看到"对话是有记忆的"——历史存在 Agent 对象里，
       每次 reply() 都会把之前几轮一起带进模型，所以第二句里的"它"它听得懂。
    """
    while True:  # 死循环：靠下面的 return 退出
        # Ctrl+C 和 Ctrl+D（管道结束）都当作"我要走了"，而不是抛一堆堆栈
        try:
            # input() 是阻塞式读取一行；strip() 去掉首尾空白（含换行）
            text = input(PROMPT).strip()
        # KeyboardInterrupt：Ctrl+C；EOFError：输入流结束（Ctrl+D 或管道关闭）
        except (KeyboardInterrupt, EOFError):
            print("\n（已退出）")
            return  # 结束整个对话循环

        if not text:  # 空行直接跳过，不浪费一次模型调用
            continue
        if text.lower() in EXIT_WORDS:  # 命中退出词就收工
            print("（已退出）")
            return

        try:
            # reply 内部会跑完整个「推理-行动」循环，返回最终消息
            # 把用户输入包装成 UserMsg 交给 Agent，Agent 自己维护历史
            reply = await agent.reply(UserMsg(name="user", content=text))
            # get_text_content() 取出消息里的纯文本部分并打印
            print(reply.get_text_content())
        except Exception as exc:  # noqa: BLE001
            # 网络抖动、key 过期、额度用光——都只影响这一轮，不该把整个会话打死。
            # 第 12 章会把这里换成框架自带的错误对象。
            # type(exc).__name__ 打出异常类名，便于快速定位是哪类问题
            print(f"[出错] {type(exc).__name__}: {exc}")
            print("  先查 .env 里的 BASE_URL 与 MODEL 是否配对，常见错误见 1.5 节。")


async def main() -> None:
    # 创建 Agent 实例：名字、人设、模型三样齐了就能聊
    agent = Agent(
        name="Buddy",  # 它自己的名字，会出现在消息的 name 字段里
        system_prompt=(  # 系统提示 = 人设与行为约束，每轮都会带给模型
            "你叫 Buddy，是一个住在命令行里的工作助手。"
            "说话直接、简短，不超过两句话。"
        ),
        model=build_model(),  # 复用上面造的模型
    )

    print("Buddy 已就位（第 1 章版本：还没有工具，只能聊天。输入 /exit 退出）")
    await chat_loop(agent)  # 进入对话循环，直到用户退出


if __name__ == "__main__":  # 只有直接运行本文件时才执行，被 import 时不执行
    # except Exception 抓不到 KeyboardInterrupt（它是 BaseException），
    # 所以"模型正在输出时按 Ctrl+C"要在这里接住，否则会弹一堆堆栈。
    try:
        asyncio.run(main())  # 启动事件循环，运行 main() 协程
    except KeyboardInterrupt:
        print("\n（已中断）")
