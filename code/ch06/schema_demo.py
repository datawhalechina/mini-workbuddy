"""第 6 章：用 Pydantic 定义工具参数。

    python code/ch06/schema_demo.py

不依赖 API key，也不用联网。
"""

import json

from pydantic import BaseModel, Field, ValidationError


class ReportInput(BaseModel):
    """生成报告的参数。"""

    topic: str = Field(description="报告主题，例如「本周工作」")
    sections: list[str] = Field(
        default_factory=list,
        description="报告的章节名列表，按顺序输出",
    )
    style: str = Field(
        default="markdown",
        description="输出格式，可选 markdown 或 plain",
    )


def main() -> None:
    print("--- 1. 生成 JSON Schema")
    print(json.dumps(ReportInput.model_json_schema(), ensure_ascii=False, indent=2))

    print("\n--- 2. 只传必填项，其余走默认值")
    print(ReportInput(topic="本周工作"))

    print("\n--- 3. 漏传必填项，校验在干活之前拦下来")
    try:
        ReportInput(sections=["进展"])
    except ValidationError as exc:
        print(exc)


if __name__ == "__main__":
    main()
