# 第 4 章　解剖 ReAct 循环

> 本章代码 `code/ch04/bare_loop.py`、`code/ch04/main.py`　·　读完约 20 分钟

## 4.0 上一章留下的问题

我们来回到上一章的问题，第 3 章那张往返图的最后一步，写着"再来一轮"。

在第3章的时候，一笔带过，现在要正面回答三个问题：

1. 这一轮到底转几次？
2. 什么时候停？
3. 如果它停不下来呢？

这三个问题的答案，构成了 Agent 的心脏。理解了它，后面所有章节（权限、上下文、持久化、前端）都只是在给这颗心脏加外围设备。

> 现在马上要解决agent的核心咯~~

## 4.1 先手写：不用框架，看清循环

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
        print(f"  [第 {i} 轮] 模型返回 tool_calls={len(choice.tool_calls or [])}")

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
            print(f"           → 调用 {name}({args})")
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

完整文件在 `code/ch04/bare_loop.py`，加上工具定义和参数表一共 138 行，其中循环本体不到 50 行。

直接 `python bare_loop.py` 就能跑，它会打印每一轮模型返回了几个 `tool_calls`——这个数字就是循环的脉搏。

```
>>> 现在几点？顺便数一下这句话有多少字：手写循环其实不复杂。

  [第 1 轮] 模型返回 tool_calls=2
           → 调用 get_current_time({})
           → 调用 count_text({'text': '手写循环其实不复杂。'})
  [第 2 轮] 模型返回 tool_calls=0
```

两行 `[第 N 轮]` 就是循环转了两圈：第一圈模型要了两个工具，第二圈它不再要工具，`if not choice.tool_calls` 命中，`return` 结束，整个"智能"就藏在这两圈里。

> 模型最后那段话的措辞每次都会变，但 `[第 1 轮] tool_calls=2` → `[第 2 轮] tool_calls=0` 这个骨架不会变，看输出时盯骨架，别盯大模型说了啥。

### 4.1.1 循环的终止条件是"模型不再要工具"

不是"得到了答案"，也不是"任务完成了"。判断依据只有一个：这次响应里有没有 `tool_calls`。

没有，说明模型认为手头的信息够用了，愿意直接说话。有，说明它还想要更多。

这条判断成立的前提是：模型被训练成"信息不足时倾向于要工具"。所以工具描述写得清楚，它才知道该要什么；写得不清楚，它就会硬猜——这又回到第 3 章那个话题。

### 4.1.2 每次工具调用都要原样存回历史

```python
messages.append({
    "role": "assistant",
    "content": choice.content,
    "tool_calls": [tc.model_dump() for tc in choice.tool_calls],
})
```

这一步很多人会漏。如果不把"模型提出过这个申请"存回去，下一轮模型会看到一条孤零零的工具结果，却不知道是谁要的，并且API 会直接报错，因为你违反了 `tool` 消息必须跟在对应 `tool_calls` 之后的规则。

### 4.1.3 工具结果要配对回填

```python
messages.append({
    "role": "tool",
    "tool_call_id": tc.id,     # 必须和那次调用对得上
    "content": str(result),
})
```

`tool_call_id` 就是第 2 章说的那个 id，一个调用配一个结果，数量和顺序都不能乱。模型一次要调三个工具，你就必须回三条结果，缺一条都会报错。

> 如果忘了这部分的知识点，可以再回到第2章看一下~

### 4.1.4 异常必须变成结果

```python
except Exception as exc:
    result = f"工具执行失败：{exc}"
```

如果工具抛异常就不管，程序直接崩。正确做法是把异常信息当成工具结果返回给模型——它会看到"Read 失败：文件不存在"，然后自己决定换个路径重试，或者告诉你找不到。

## 4.2 手写版漏了七件事

上面那 60 行能跑通，甚至能跑得不错。但它是一个"演示级"的循环。

![手写循环漏掉的七件事](assets/ch04/seven-gaps.png)

逐个说，因为它们就是后面几章的主线：

**并发执行**。模型一次要求读三个文件，手写版是 `for` 循环串行跑的。并发能省掉三分之二的等待时间——但前提是判断得出"这三个工具互不依赖"。

**错误回灌**。上面我们做了 try/except，但只做了最粗的一层。工具超时怎么办？返回值太大怎么办？错误信息要不要截断？

**迭代上限**。有 `max_iters`，但撞上限之后只是返回一句"达到最大轮数"。用户在界面上看到这句话，完全不知道发生了什么。框架的做法是：追加一次**禁用工具**的模型调用，逼它把手头的信息总结成一段话。

**上下文压缩**。随着agent持续调用工具，上下文会直接爆炸，手写版没有任何压缩。聊到第四十轮，`messages` 超出模型窗口，请求直接报 400。

**权限拦截**。手写版是模型说要调什么就调什么。如果它决定执行 `rm -rf ./src`，你没有插手的机会。

**中断**。用户想停，手写版没有入口。循环跑到一半被 `Ctrl+C` 打断，`messages` 就停在一个"有调用没结果"的非法状态。

**结构化输出**。你需要一个 JSON，它会回你一段"好的，我这就为您整理"。得有个机制逼它按 schema 交作业，还要在格式不对时自动重试。

> 我们正常接近agent的核心内容哟~~

## 4.3 换成 Agent：同样的任务

```python
agent = Agent(
    name="Buddy",
    system_prompt=(
        "你叫 Buddy，是一个住在命令行里的工作助手。"
        "需要用工具获取信息时必须调用工具。回答简短。"
    ),
    model=build_model(),
    toolkit=build_toolkit(),
    react_config=ReActConfig(max_iters=10),
)

# 命令行脚本没有确认界面。工具默认会"停下来等人点头"，
# 在这里就会卡在 REQUIRE_USER_CONFIRM 上。先整体放行，第 7 章再管住它。
agent.state.permission_context.mode = PermissionMode.BYPASS

async for event in agent.reply_stream(UserMsg(name="user", content=question)):
    ...
```

`build_model()` 和 `build_toolkit()` 直接复用第 3 章的写法，两个工具函数从 `bare_loop.py` 里 import——**同一个 `get_current_time`，同一份实现**。这样两边的输出才有可比性。

那行 `PermissionMode.BYPASS` 和第 3 章是同一个原因：没有界面，工具会卡在"等人点头"。第 7 章会把它换成真正的确认流程。

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

`code/ch04/main.py` 里有一个打印器，把事件流按"层级 + 耗时"排成时间线。它的核心只有三张表加一个循环：

```python
# 事件名 -> 缩进层级。只列会出现在时间线上的事件，无载荷的见 IGNORE_EVENTS
DEPTH = {
    "REPLY_START": 0,
    "REPLY_END": 0,
    "MODEL_CALL_START": 1,
    "MODEL_CALL_END": 1,
    "REQUIRE_USER_CONFIRM": 1,  # 第 7 章才有确认环节，本章走 BYPASS 不会触发
    "TOOL_CALL_START": 2,
    "TOOL_RESULT_END": 2,
}

# 不展示的事件：无载荷的过渡事件，以及框架注入的提示
IGNORE_EVENTS = {
    "TOOL_CALL_END", "TOOL_RESULT_START",
    "TEXT_BLOCK_START", "TEXT_BLOCK_END",
    "THINKING_BLOCK_START", "THINKING_BLOCK_END",
    "HINT_BLOCK",
}

async def run_with_trace(agent: Agent, question: str) -> None:
    start = time.perf_counter()
    async for event in agent.reply_stream(UserMsg(name="user", content=question)):
        kind = event.type.value
        elapsed = (time.perf_counter() - start) * 1000

        # 逐字增量与无载荷事件都不进时间线
        if kind.endswith("_DELTA") or kind in IGNORE_EVENTS:
            continue

        indent = "  " * DEPTH.get(kind, 1)
        print(f"{elapsed:8.0f}ms  {indent}{kind:<22} {payload(event)}")
```

`DEPTH` 决定缩进，`IGNORE_EVENTS` 决定哪些不上屏，`payload()` 负责从事件里取出值得看的那一两个字段（工具名、token 数、结束原因）。**没有一行是装饰**——第 12 章的前端就是把这三样东西换成 DOM 节点。

上面为了看清主干，省掉了两段：一段累计 `MODEL_CALL_START` / `TOOL_CALL_START` 的个数和 token，用来打最后那行汇总；一段把 `TEXT_BLOCK_DELTA` 攒起来，在 `MODEL_CALL_END` 时整段印出模型这一轮说的话。完整版在 `code/ch04/main.py`，跑起来就能看到它们的效果。

直接 `python main.py`，实测输出：

```
>>> 你：现在几点？顺便数一下这句话有多少字：手写循环其实不复杂。
------------------------------------------------------------------------
       0ms  REPLY_START
       4ms    MODEL_CALL_START
    1445ms      TOOL_CALL_START       get_current_time
    1632ms      TOOL_CALL_START       count_text
    1817ms    MODEL_CALL_END         in=422 out=132
    1980ms      TOOL_RESULT_END       success
    1980ms      TOOL_RESULT_END       success
    1987ms    MODEL_CALL_START
    3715ms    MODEL_CALL_END         in=524 out=141

- 现在：2026-10-30 07:57 UTC（星期五；工具返回的本地时间为 15:57）
- 「手写循环其实不复杂。」：共 10 个字符，其中中文 9 个字（含句号）

    3715ms  REPLY_END              completed
------------------------------------------------------------------------
模型调用 2 次｜工具调用 2 次｜token in=946 out=273｜总耗时 3715ms
```

对照 4.1 手写版的输出，两边的结构完全一样：都是"模型调用 2 次、工具调用 2 次"。区别只在于手写版只打印了 `[第 N 轮]` 两行，而这里把每一拍都摊开了。

> 上面的耗时和 token 数会因模型、网络而异；**结构**才是要注意的地方。打印器只留有信息量的几类事件，`TEXT_BLOCK_START`、`TOOL_CALL_END` 这类空壳和 `HINT_BLOCK` 这类框架注入的提示都不上时间线。

这里有个反直觉的顺序值得停一下：**`MODEL_CALL_END` 比 `TOOL_RESULT_END` 早**。原因是模型一边生成一边就把"我要调哪两个工具"声明出来了（`TOOL_CALL_START` 在流里发出），等这段生成彻底结束才发 `MODEL_CALL_END`；而工具是拿到完整调用参数之后才真正执行的，结果自然排在后面。所以时间线上的时间戳顺序不等于因果顺序——读的时候按缩进层级看，别按行号看。

看这张时间线，能读出三件事：

**第一，模型调用是瓶颈。** 两次调用加起来约 3.5 秒，占了总耗时 3.7 秒的绝大部分；工具执行只花了 163 毫秒。这解释了为什么第 8 章的上下文压缩那么重要——省 token 就是省钱，省钱就是省时间。

**第二，工具是并发的。**两个 `TOOL_CALL_START` 连着出现，没有等彼此。这就是 4.2 节说的"并发执行"。

**第三，`REPLY_END` 的 `finished_reason` 是 `completed`。**如果它变成 `exceed_max_iters`，说明模型转晕了；变成 `error`，说明某一步炸了。**前端要按这个字段给用户不同的说法。**

这个打印器的价值在于：它是第 12 章前端的原型。前端做的事，就是把这张时间线画成界面——把 `MODEL_CALL_START` 变成"正在输入"，把 `TOOL_CALL_START` 变成一张卡片，把 `TEXT_BLOCK_DELTA` 变成逐字打印。

## 4.5 控制循环：`ReActConfig`

```python
from agentscope.agent import ReActConfig

react_config=ReActConfig(
    max_iters=10,              # 最多转 10 轮
    stop_on_reject=False,      # 工具被拒绝时不终止
    structured_output_grace_iters=5,
)
```

ReActConfig 一共五个字段：

| 参数 | 默认值 | 作用 |
|---|---|---|
| `max_iters` | 50 | 最多多少轮推理-行动迭代 |
| `structured_output_grace_iters` | 5 | 超过 `max_iters` 后额外允许的轮数，专门用来产出结构化输出 |
| `stop_on_reject` | `False` | 工具调用被拒绝时，是否立刻终止回复 |
| `interruption_message` | `'I notice the interruption. How can I help you?'` | 被中断时补的那句话 |
| `interruption_raise_cancelled_error` | `False` | 中断时是否抛 `CancelledError`，而不是正常收尾 |

`max_iters` 默认 50，这个值比大多数人的直觉大得多。为什么？因为复杂任务确实需要转很多圈：读十个文件、改五处代码、跑三次测试，五十轮并不夸张。

但它也是一个成本开关。**每一次循环都是一次 API 调用**，50 轮意味着最坏情况下 50 次请求。所以示例里把它压到 10——不是因为它不够用，而是因为你要先知道自己在花多少钱。

> **撞上限之后会发生什么**
> 不是立刻结束。`cur_iter` 到了 `max_iters`，框架先进入**宽限期**（`structured_output_grace_iters`，默认 5 轮）：这几轮里它给模型注入一条 `<system-reminder>`，并用 `tool_choice` 把可选工具限定成结构化输出工具，逼模型把结果吐出来。
> 宽限期也烧完（`cur_iter >= max_iters + grace_iters`），才发 `EXCEED_MAX_ITERS` 事件、以 `finished_reason=exceed_max_iters` 收场。这个事件在 2.x 里已经标了 deprecated，只为向后兼容而保留，别拿它当主信号。
> 还有一个更容易踩的点：**只要结束时 `cur_iter > max_iters`，`finished_reason` 就是 `exceed_max_iters`，哪怕模型已经写完了正文。** 所以别用 `finished_reason` 判断"有没有拿到内容"，那要看 `get_text_content()`。
> 这个细节的价值在于：它保证"超时"不会给用户一个空白界面——宽限期里模型往往已经能把该说的说完。

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
- 手写循环不到 50 行就能跑，但漏了七件事：并发、错误回灌、上限、压缩、权限、中断、结构化输出
- `Agent` 把循环藏了起来，用**事件流**暴露过程
- 事件时间线是前端界面的原型：`MODEL_CALL_START` → 正在输入，`TOOL_CALL_START` → 工具卡片
- `ReActConfig.max_iters` 默认 50，是成本开关，也是安全阀

**下一章要解决的问题**：第 4.1 节的裸循环有一个不易察觉的隐患——工具的工作目录，就是你的家目录。模型说要读 `~/Documents/工资条.xlsx`，它就去读；说要往 `~/Desktop` 写文件，它就写。**它没有"工作边界"这个概念**。
