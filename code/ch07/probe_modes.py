"""第 7 章配套实验：权限判定的离线对照表（不需要模型，不需要 key）。

直接调用 PermissionEngine，看同一批工具调用在不同模式下的判定结果。
运行：python probe_modes.py
"""

import asyncio
import os

from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from agentscope.tool import Bash, Read, Write

COMMANDS = [
    "ls -la",
    "rm -rf /",
    "echo hi >> ~/.bashrc",
]


async def show_three_cases() -> None:
    """7.1 用。同一套默认设置，三件事三个答案。"""
    cases = [
        (
            "读一下 readme.md",
            Read(),
            {"file_path": os.path.abspath("./sandbox/readme.md")},
        ),
        (
            "改一下 notes.md",
            Write(),
            {"file_path": os.path.abspath("./sandbox/notes.md"), "content": "hello"},
        ),
        (
            "往 ~/.bashrc 追加一行",
            Bash(),
            {"command": "echo hi >> ~/.bashrc"},
        ),
    ]

    print("--- 1. 同一套默认设置，三件事三个答案")
    engine = PermissionEngine(PermissionContext(mode=PermissionMode.DEFAULT))
    for label, tool, args in cases:
        decision = await engine.check_permission(tool, args)
        print(f"  {label} -> {decision.behavior.value}  immune={decision.bypass_immune}")

    # 给 Bash 补一条"全放行"规则，看第三件事会不会变
    engine.add_rule(
        PermissionRule(
            tool_name="Bash",
            rule_content="",
            behavior=PermissionBehavior.ALLOW,
            source="user",
        ),
    )
    decision = await engine.check_permission(Bash(), {"command": "echo hi >> ~/.bashrc"})
    print("  补一条 Bash 全放行规则，再问第三件")
    print(
        f"  往 ~/.bashrc 追加一行 -> {decision.behavior.value}  "
        f"immune={decision.bypass_immune}   规则没盖住",
    )
    print()


async def show_mode_table() -> None:
    """7.2 用。五种模式 × 三类命令。"""
    tool = Bash()

    print("--- 2. 五种模式 × 三类命令")
    header = f"{'mode':<14}{'command':<24}{'decision':<10}{'immune':<9}reason"
    print(header)
    print("-" * 100)

    for mode in PermissionMode:
        engine = PermissionEngine(PermissionContext(mode=mode))
        for command in COMMANDS:
            decision = await engine.check_permission(tool, {"command": command})
            print(
                f"{mode.value:<14}{command:<24}"
                f"{decision.behavior.value:<10}"
                f"{str(decision.bypass_immune):<9}"
                f"{decision.message[:36]}",
            )
        print()


async def show_rule_limits() -> None:
    """7.6 用。两条规则实验，看 allow 能不能盖住安全拦截。"""
    tool = Bash()

    print("--- 3. 规则能不能盖住安全拦截")
    context = PermissionContext(mode=PermissionMode.DEFAULT)
    engine = PermissionEngine(context)
    engine.add_rule(
        PermissionRule(
            tool_name="Bash",
            rule_content="rm -rf",
            behavior=PermissionBehavior.ALLOW,
            source="user",
        ),
    )

    print("已写入 allow 规则：Bash / rm -rf")

    for command in ("rm -rf /", "rm -rf ./build"):
        decision = await engine.check_permission(tool, {"command": command})
        print(
            f"  {command:<18} -> {decision.behavior.value:<6}"
            f"immune={decision.bypass_immune}　{decision.message[:40]}",
        )

    # 再写一条 deny 规则，看它能否盖住"只读放行"
    engine.add_rule(
        PermissionRule(
            tool_name="Bash",
            rule_content="ls",
            behavior=PermissionBehavior.DENY,
            source="user",
        ),
    )
    decision = await engine.check_permission(tool, {"command": "ls -la"})
    print(f"  {'ls -la':<18} -> {decision.behavior.value:<6}（deny 规则压过只读快通道）")

    # Write 工具在 DEFAULT 模式下，写工作区内也要问
    await show_write_case()


async def show_write_case() -> None:
    """同一个 Write，两种模式两种结果。"""
    tool = Write()
    target = os.path.abspath("./sandbox/notes.md")

    for mode in (PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS):
        engine = PermissionEngine(PermissionContext(mode=mode))
        decision = await engine.check_permission(
            tool,
            {"file_path": target, "content": "hello"},
        )
        print(
            f"  Write {mode.value:<13} -> {decision.behavior.value:<6}"
            f"{decision.message[:44]}",
        )


async def main() -> None:
    await show_three_cases()
    await show_mode_table()
    await show_rule_limits()


if __name__ == "__main__":
    asyncio.run(main())
