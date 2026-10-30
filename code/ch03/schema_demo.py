"""第 3 章附件：不用 API key，直接看清"函数是怎么变成工具定义的"。

运行：python schema_demo.py

三件事：
  1. 一个普通函数 + 类型注解 + docstring，被 FunctionTool 转成 JSON Schema
  2. 逐字段对照：你写的哪一行，变成了模型看到的哪一项
  3. 反例：少了类型注解、docstring 写得太敷衍，schema 会变成什么样
"""

import asyncio
import json

from agentscope.tool import FunctionTool, Toolkit


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


async def dump(func) -> dict:
    """把单个函数转成 schema 并返回（Toolkit 的 get_tool_schemas 是异步的）。"""
    toolkit = Toolkit(tools=[FunctionTool(func)])
    schemas = await toolkit.get_tool_schemas()
    return schemas[0]


async def demo_full() -> None:
    """1 & 2. 完整写法：四样东西都被用上了。"""
    print("=" * 78)
    print("一、完整写法：函数名 / docstring / 类型注解 / Args 说明")
    print("=" * 78)

    schema = await dump(count_text)
    print(json.dumps(schema, ensure_ascii=False, indent=2))
    print()

    fn = schema["function"]
    param = fn["parameters"]["properties"]["text"]
    print("逐字段对照：")
    print(f"  函数名 count_text        -> name        = {fn['name']}")
    print(f"  docstring 全文           -> description = {fn['description']}")
    print(f"  类型注解 text: str       -> type        = {param['type']}")
    print(f"  Args 下的说明            -> description = {param['description']}")
    print(f"  有默认值吗？没有         -> required    = {fn['parameters']['required']}")
    print()


async def demo_bad() -> None:
    """3. 两个反例：模型会因此调不好工具。"""
    print("=" * 78)
    print("二、反例：同样的逻辑，schema 缺了关键信息")
    print("=" * 78)

    def no_annotation(text):
        """统计一段文本的字符数、中文字数与行数。"""
        return ""

    def lazy_docstring(text: str) -> str:
        """统计文本。"""
        return ""

    bad1 = await dump(no_annotation)
    print("反例 1：函数没有类型注解")
    print(json.dumps(bad1["function"]["parameters"], ensure_ascii=False, indent=2))
    print("  → text 只剩一个空对象 {}，没有 type、没有说明。")
    print("    模型知道有个叫 text 的参数，但不知道它该填字符串还是数字。")
    print()

    bad2 = await dump(lazy_docstring)
    print("反例 2：docstring 只写了「统计文本。」")
    print(f"  description = {bad2['function']['description']}")
    print("  → 模型无法判断什么时候该用它：统计字数？词频？行数？")
    print()

    print("结论：schema 不是框架的魔法，它只是把你写的注释和注解原样翻译过去。")
    print("      注释写得含糊，模型看到的世界就含糊。")


async def main() -> None:
    await demo_full()
    await demo_bad()


if __name__ == "__main__":
    asyncio.run(main())
