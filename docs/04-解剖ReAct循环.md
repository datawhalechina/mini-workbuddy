# 第 4 章　解剖 ReAct 循环

> 本章代码 `code/ch04/bare_loop.py`、`code/ch04/main.py`　·　读完约 20 分钟

## 4.0 上一章留下的问题

第 3 章那张往返图的最后一步，写着"再来一轮"。当时一带而过，现在要正面回答三个问题：

1. 这一轮到底转几次？
2. 什么时候停？
3. 如果它停不下来呢？

这三个问题的答案，构成了 Agent 的心脏。理解了它，后面所有章节（权限、上下文、持久化、前端）都只是在给这颗心脏加外围设备。

理解它的最好方式不是读文档，而是**自己写一遍**。

## 4.1 先手写：60 行，不用框架

下面这段代码没有 `import agentscope`。它直接用 OpenAI SDK，把上一章那张往返图跑起来：

```python
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

        # 没有工具调用，说明它准备直接回答，循环结束
        if not choice.tool_calls:
            return choice.content or ""

        # 有工具调用：把这次 assistant 消息原样存回历史
        messages.append({
            "role": "assistant",
            "content": choice.content,
            "tool_calls": [tc.model_dump() for tc in choice.tool_calls],
        })

        # 逐个执行，把结果作为 tool 消息追加
        for tc in choice.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            try:
                result = REGISTRY[name](**args)
            except Exception as exc:      # 工具炸了不能把整个程序带崩
                result = f"工具执行失败：{exc}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })
        # 回到循环开头，再问一次模型
```

完整文件在 `code/ch04/bare_loop.py`，加上工具定义和参数表大约 60 行。

这段代码里有四个必须看懂的点：

### 4.1.1 循环的终止条件是"模型不再要工具"

不是"得到了答案"，也不是"任务完成了"。判断依据只有一个：这次响应里有没有 `tool_calls`。

没有，说明模型认为手头的信息够用了，愿意直接说话。有，说明它还想要更多。

这条判断成立的前提是：**模型被训练成"信息不足时倾向于要工具"**。所以工具描述写得清楚，它才知道该要什么；写得不清楚，它就会硬猜——这又回到第 3 章那个话题。

### 4.1.2 每次工具调用都要原样存回历史

```python
messages.append({
    "role": "assistant",
    "content": choice.content,
    "tool_calls": [tc.model_dump() for tc in choice.tool_calls],
})
```

这一步很多人会漏。如果不把"模型提出过这个申请"存回去，下一轮模型会看到一条孤零零的工具结果，却不知道是谁要的——API 会直接报错，因为你违反了 `tool` 消息必须跟在对应 `tool_calls` 之后的规则。

### 4.1.3 工具结果要配对回填

```python
messages.append({
    "role": "tool",
    "tool_call_id": tc.id,     # 必须和那次调用对得上
    "content": str(result),
})
```

`tool_call_id` 就是第 2 章说的那个 id。**一个调用配一个结果，数量和顺序都不能乱**。模型一次要调三个工具，你就必须回三条结果，缺一条都会报错。

### 4.1.4 异常必须变成结果

```python
except Exception as exc:
    result = f"工具执行失败：{exc}"
```

这是手写版里唯一一句"防御性代码"，也是最有价值的一句。

如果工具抛异常就不管，程序直接崩。正确做法是把异常信息当成工具结果返回给模型——它会看到"Read 失败：文件不存在"，然后自己决定换个路径重试，或者告诉你找不到。

**模型有自愈能力，前提是你把错误告诉它。**

## 4.2 手写版漏了七件事

上面那 60 行能跑通，甚至能跑得不错。但它是一个"演示级"的循环。

![手写循环漏掉的七件事](assets/ch04/seven-gaps.png)

逐个说，因为它们就是后面几章的主线：

**并发执行**。模型一次要求读三个文件，手写版是 `for` 循环串行跑的。并发能省掉三分之二的等待时间——但前提是判断得出"这三个工具互不依赖"。

**错误回灌**。上面我们做了 try/except，但只做了最粗的一层。工具超时怎么办？返回值太大怎么办？错误信息要不要截断？

**迭代上限**。有 `max_iters`，但撞上限之后只是返回一句"达到最大轮数"。用户在界面上看到这句话，完全不知道发生了什么。框架的做法是：追加一次**禁用工具**的模型调用，逼它把手头的信息总结成一段话。

**上下文压缩**。手写版没有任何压缩。聊到第四十轮，`messages` 超出模型窗口，请求直接报 400。

**权限拦截**。手写版是模型说要调什么就调什么。如果它决定执行 `rm -rf ./src`，你没有插手的机会。

**中断**。用户想停，手写版没有入口。循环跑到一半被 `Ctrl+C` 打断，`messages` 就停在一个"有调用没结果"的非法状态。

**结构化输出**。你需要一个 JSON，它会回你一段"好的，我这就为您整理"。得有个机制逼它按 schema 交作业，还要在格式不对时自动重试。

这七件事，一件一件补，就是 AgentScope 的 `Agent` 类。

## 4.3 换成 Agent：同样的任务

```python
agent = Agent(
    name="Buddy",
    system_prompt="你叫 Buddy，需要用工具获取信息时必须调用工具。回答简短。",
    model=build_model(),
    toolkit=build_toolkit(),
    react_config=ReActConfig(max_iters=10),
)

async for event in agent.reply_stream(UserMsg(name="user", content=question)):
    ...
```

循环本身消失了。`Agent` 内部有它，但你看不到 `for i in range(...)`，也看不到 `messages.append(...)`。

取而代之的是**事件**。这是本章第二个重点：循环没有消失，它变成了可观察的事件流。

![ReAct 循环的三个分支](assets/ch04/react-loop.png)

每一轮，`Agent` 在一个统一的决策点检查当前状态，然后选一条路走：

| 分支 | 触发条件 | 产出 |
|---|---|---|
| 推理 | 需要问模型 | `MODEL_CALL_START` 及后续内容块事件 |
| 行动 | 模型要求调用工具 | `TOOL_CALL_*` / `TOOL_RESULT_*` |
| 退出 | 模型不再要工具，或撞上限 | `REPLY_END` |

第 2 章那张事件表，现在有了因果解释：**为什么会有 `MODEL_CALL_START` 出现第二次、第三次？因为循环转了不止一圈。**

## 4.4 事件时间线：把循环的每一拍摊开

`code/ch04/main.py` 里有一个打印器，把事件流按"层级 + 耗时"排成时间线：

```
>>> 你：现在几点？顺便数一下这句话有多少字：手写循环其实不复杂。
------------------------------------------------------------------------
       0ms  REPLY_START
       2ms    MODEL_CALL_START
    1830ms    MODEL_CALL_END          in=412 out=58
    1832ms      TOOL_CALL_START       get_current_time
    1834ms      TOOL_RESULT_END       success
    1836ms      TOOL_CALL_START       count_text
    1838ms      TOOL_RESULT_END       success
    1840ms    MODEL_CALL_START
    3021ms    MODEL_CALL_END          in=536 out=74
现在是 2026-10-22 09:53:07，星期四。这句话共 13 个字符，其中中文 11 个字。
    3023ms  REPLY_END                completed
------------------------------------------------------------------------
模型调用 2 次｜工具调用 2 次｜token in=948 out=132｜总耗时 3023ms
```

> 上面的耗时和 token 数会因模型、网络而异；**结构**才是要注意的地方。

看这张时间线，能读出三件事：

**第一，模型调用是瓶颈。** 两次调用（MODEL_CALL_START）占了三秒里的绝大部分，工具执行（TOOL_CALL_START）只花了 6 毫秒。这解释了为什么第 8 章的上下文压缩那么重要——省 token 就是省钱，省钱就是省时间。

**第二，工具是并发的。** 两个 `TOOL_CALL_START` 连着出现，没有等彼此。这就是 4.2 节说的"并发执行"。

**第三，`REPLY_END` 的 `finished_reason` 是 `completed`。** 如果它变成 `exceed_max_iters`，说明模型转晕了；变成 `error`，说明某一步炸了。**前端要按这个字段给用户不同的说法。**

这个打印器的价值在于：它是第 12 章前端的原型。前端做的事，就是把这张时间线画成界面——把 `MODEL_CALL_START` 变成"正在输入"，把 `TOOL_CALL_START` 变成一张卡片，把 `TEXT_BLOCK_DELTA` 变成逐字打印。

## 4.5 控制循环：`ReActConfig`

```python
from agentscope.agent import ReActConfig

react_config=ReActConfig(
    max_iters=10,              # 最多转 10 轮
    stop_on_reject=False,      # 工具调用被拒绝时，是否立刻终止回复
    structured_output_grace_iters=5,   # 结构化输出失败时，额外给几次机会
)
```

ReActConfig方法中`max_iters` 默认值是 50，这个值比大多数人的直觉大得多。为什么？因为复杂任务确实需要转很多圈：读十个文件、改五处代码、跑三次测试，五十轮并不夸张。

但它也是一个成本开关。**每一次循环都是一次 API 调用**，50 轮意味着最坏情况下 50 次请求。所以示例里把它压到 10——不是因为它不够用，而是因为你要先知道自己在花多少钱。

> **撞上限之后会发生什么**
> `finished_reason` 变成 `exceed_max_iters`。框架还会追加**一次**禁用工具的模型调用，要求它把已完成的工作总结出来。这次收尾调用只做一次，如果它仍然没有产出文本，回复才以空文本结束。
> 这个细节很重要：它保证了"超时"不会给用户一个空白界面。

## 4.6 三个常见错误

**错误 1：以为 `reply()` 只调用一次模型**

```python
reply = await agent.reply(msg)   # 内部可能调用了 8 次模型
```

如果按"一次 reply = 一次 API 调用"来估算成本，会严重低估。正确做法是读 `reply.usage`，或者数 `MODEL_CALL_END` 事件的个数。

**错误 2：把 `TOOL_CALL_START` 当成"工具已经执行完了"**

`TOOL_CALL_START` 只表示模型提出了申请。工具可能还在执行（`TOOL_RESULT_START`），可能被权限拦下了（`REQUIRE_USER_CONFIRM`），也可能失败了（`TOOL_RESULT_END` 且 `state="error"`）。界面上应该在这三个时刻显示不同状态。

**错误 3：在 `system_prompt` 里写"最多使用三个工具"**

模型对这类数量约束的执行力很弱，而 `max_iters` 是硬约束。**能用配置解决的，不要用提示词解决。**

## 4.7 小结

- 循环的终止条件是**模型不再请求工具**，不是"任务完成"
- 手写循环 60 行能跑，但漏了七件事：并发、错误回灌、上限、压缩、权限、中断、结构化输出
- `Agent` 把循环藏了起来，用**事件流**暴露过程
- 事件时间线是前端界面的原型：`MODEL_CALL_START` → 正在输入，`TOOL_CALL_START` → 工具卡片
- `ReActConfig.max_iters` 默认 50，是成本开关，也是安全阀

**下一章要解决的问题**：第 4.1 节的裸循环有一个不易察觉的隐患——工具的工作目录，就是你的家目录。模型说要读 `~/Documents/工资条.xlsx`，它就去读；说要往 `~/Desktop` 写文件，它就写。**它没有"工作边界"这个概念**。
