# 第 4 章　解剖 ReAct 循环

> 本章代码 `code/ch04/bare_loop.py`、`code/ch04/main.py`　·　读完约 20 分钟

## 4.0 上一章留下的问题

第 3 章那张工具往返图，第 ⑤ 步写的是"模型若还要调工具，就回到第 ② 步"。

当时一句话就带过了，因为它的答案得留到这一章。现在回头，把它正面拆开：

1. 这一轮到底转几次？
2. 什么时候停？
3. 如果它停不下来呢？

这三个问题的答案，构成了 Agent 的机芯。理解了它，后面几章（权限、上下文、持久化、前端）全都是在给这块机芯装表壳、上表盘、配日历窗。

> 准备好了吗？我们把后盖拧开，看看里面到底有几个齿轮~~

## 4.1 先手写：不用框架，看清循环

在搬出框架之前，我们先挽起袖子手写一遍。注意这段代码里没有 `import agentscope`——它直接用 OpenAI SDK，把上一章那张往返图跑起来。

> 就像学开车先摸手动挡——离合什么时候踩、挡什么时候换，脚上有了感觉，再开自动挡才知道变速箱在替你干什么。

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
            try:
                if name not in REGISTRY:      # 模型偶尔会编出不存在的工具名
                    raise LookupError(
                        f"没有名为 {name} 的工具。可用工具：{', '.join(REGISTRY)}",
                    )
                args = json.loads(tc.function.arguments or "{}")
                print(f"           → 调用 {name}({args})")
                result = REGISTRY[name](**args)
            except Exception as exc:          # 解析失败、工具炸了，都只影响这一条
                result = f"工具执行失败：{exc}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })
        # 回到循环开头，再问一次模型

    return (
        f"（循环转满 {max_iters} 轮仍未收敛，任务没有完成。"
        f"可以把 max_iters 调大后重试）"
    )
```

为了看清主干，上面删掉了部分注释和换行；完整文件在 `code/ch04/bare_loop.py`，加上工具定义和参数表一共 153 行，其中循环本体（`bare_loop` 这个函数）60 行。

直接 `python bare_loop.py` 就能跑，它会打印每一轮模型返回了几个 `tool_calls`——这个数字就是机芯的节拍。

```
>>> 现在几点？顺便数一下这句话有多少字：手写循环其实不复杂。

  [第 1 轮] 模型返回 tool_calls=2
           → 调用 get_current_time({})
           → 调用 count_text({'text': '手写循环其实不复杂。'})
  [第 2 轮] 模型返回 tool_calls=0
```

两行 `[第 N 轮]`，就是循环转了两圈。第一圈模型要了两个工具，第二圈它不再要工具，`if not choice.tool_calls` 命中，`return` 结束。

整个"智能"，就藏在这两圈里。听着挺玄，写完你大概会想，就这？

> 模型最后那段话的措辞每次都会变，但 `[第 1 轮] tool_calls=2` → `[第 2 轮] tool_calls=0` 这个骨架不会变。看输出时盯骨架，别盯大模型说了啥。
>
> 骨架看清楚了，不过这段代码真正容易写错的地方，不在"转圈"本身，而在圈里的四件小事——凭什么停（4.1.1）、模型提出过的申请为什么要存回去（4.1.2）、工具结果怎么配对（4.1.3）、工具炸了怎么办（4.1.4）。
>
> 那我们就继续出发吧~

### 4.1.1 循环什么时候停：模型不再要工具

先说第一件，也是大家最容易忽略的一件。循环凭什么停？

很多人以为，循环会一直转到"任务做完"为止——做完了，它自然就停。

这话大体没错，但"做没做完"只有模型自己知道，你手上能看见的信号只有一个，就是这次响应里有没有 `tool_calls`。没有，说明模型觉得手头的信息够用了，愿意直接开口回答；只要还有 `tool_calls`，它就还想要更多。

这条判断能成立，前提是模型愿意主动伸手要工具。它什么时候愿意？多半看工具描述写得清不清楚——描述到位，它一眼就能对上"这个问题该用哪个工具"，于是发起调用；描述含糊，它宁可凭记忆硬答，也不愿去调一个自己都看不懂的函数。这又回到第 3 章那个话题。

不过这只是正常情况。还有三种情况会让循环被迫停下，比如撞上 `max_iters`、用户中断、工具抛异常。那时候循环也会退出，只是退出的方式不一样（4.5 节讲上限，第 13 章讲中断）。

还有个坑，藏得比较深。

模型的输出是有长度上限的（请求里的 `max_tokens`，或者模型自身的能力上限）。一旦撞上那个上限，话就说到一半被掐断，响应里的 `finish_reason` 会变成 `length`——而不是 `stop`，意思很直白："我没说完。"

掐在哪儿，后果不一样：

- **掐在正文中间**——一个 `tool_calls` 都没有，循环按"正常结束"处理，`return choice.content` 交出去的是一句半截话，比如"现在的北京时间是 2026-10-30 07:5"。它不报错，这句话就这么显示给用户了。
- **掐在工具参数里**——模型正在写 `{"text": "手写循环其实不复`，参数 JSON 还没闭合就被掐断。这时 `tool_calls` 是有的，但 `json.loads` 拿到的是半截字符串，直接抛 `JSONDecodeError`——这正是 4.1.4 那个 `try` 要连解析一起包住的原因。

所以只盯 `tool_calls` 还不够，`finish_reason` 也得扫一眼，`stop` 是正常说完，`tool_calls` 是要调工具，`length` 是被截断，`content_filter` 是被安全策略拦下。前两种放心往下走，后两种得当异常处理。

### 4.1.2 每次工具调用都要原样存回历史

4.1.1 说的是"循环什么时候结束"，这一节说的是"每一圈里留下了什么"。

先明确一下，"历史"指的就是 `messages` 这个列表。每次调用模型，你都把它整个发过去——模型之所以"记得"前几轮聊过什么，不是它自带记忆，而是你每次都把这份记录重新递了一遍。

现在看模型这一轮的回复。它不只有一句话，还可能附带一张"工具申请单"，也就是 `tool_calls`，写明要调哪几个函数、参数填什么。这张单子必须和它的文字一起，作为一条 `assistant` 消息存进 `messages`：

```python
messages.append({
    "role": "assistant",
    "content": choice.content,                                     # 它说的话
    "tool_calls": [tc.model_dump() for tc in choice.tool_calls],    # 它开的申请单
})
```

> `tc.model_dump()` 只是把 SDK 的对象转成普通 dict，方便原样塞回请求。想手写也行，但 `id`、`type`、`function` 里的 `name` 和 `arguments` 一个都不能丢——下一小节配对，靠的就是 `id`。

这一步很多人会漏。漏了会怎样？把 `messages` 打印出来看最直观。

存对了，历史长这样——一条申请单，紧跟一条处理结果：

```json
[
  {"role": "user", "content": "现在几点？"},
  {
    "role": "assistant",
    "content": "",
    "tool_calls": [
      {"id": "call_abc", "function": {"name": "get_current_time", "arguments": "{}"}}
    ]
  },
  {"role": "tool", "tool_call_id": "call_abc", "content": "2026-10-30 15:57:21"}
]
```

漏了那条 `assistant` 呢？它变成这样：

```json
[
  {"role": "user", "content": "现在几点？"},
  {"role": "tool", "tool_call_id": "call_abc", "content": "2026-10-30 15:57:21"}
]
```

第二份记录里，那条 `tool` 消息在说"`call_abc` 这事儿办完了"，可整段历史里根本找不到 `call_abc` 这张单子。API 校验时就卡在这儿，直接返回 400，报错信息大概是 `messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。

这就是为什么申请单必须留着——它不只是给模型看的上下文，还是那条 `tool` 消息的"出身证明"。（`tool` 消息的写法下一小节细说，这里先看结构。）

### 4.1.3 工具结果要配对回填

申请单存好了，结果也回来了，现在把结果写回历史——关键是让每条结果都带上"我回答的是哪张单子"。这个编号就是申请单里的 `tc.id`，填进 `tool_call_id`：

```python
# tc 就是当前这张申请单，tc.id 就是它的编号
messages.append({
    "role": "tool",
    "tool_call_id": tc.id,     # 回答的是哪一张单子
    "content": str(result),    # 办完的结果；API 只收字符串，所以要转一下
})
```

对照 4.1.2 那份历史，`assistant` 里的 `"id": "call_abc"`，和 `tool` 里的 `"tool_call_id": "call_abc"` 是同一个值。这就是"配对"的全部含义——API 靠这个编号把结果认领回具体的某张单子，而不是靠它排在第几个。

所以有一条申请单，就必须回一条结果。模型一次要调三个工具，你就得回三条 `tool` 消息，各自带好自己的 `tool_call_id`；少一条，API 就报错（大意是"每个 `tool_calls` 都要有对应的 `tool` 消息"）。

顺序上建议照着申请单的次序来。大多数服务是按 `tool_call_id` 匹配的，按顺序排不会吃亏，打乱了却可能在别的实现上出问题——没必要冒这个险。

> 编号是从哪来的，要是记不清了，翻回第 2 章看一眼 `event_ids.py` 的输出~

### 4.1.4 异常必须变成结果

前面三节，默认的都是"工具能顺利跑完"。可现实里还有另一半——它跑不完。参数是坏的，工具名是编的，真跑起来还会炸（文件不在、网络断了、没权限）。

这些要是接不住，Agent 就成了一次性的，模型错一次，会话就报废。所以这一节干的，是把失败也变成一条能继续往下传的消息。

```python
try:
    if name not in REGISTRY:
        raise LookupError(
            f"没有名为 {name} 的工具。可用工具：{', '.join(REGISTRY)}",
        )
    args = json.loads(tc.function.arguments or "{}")
    result = REGISTRY[name](**args)
except Exception as exc:
    result = f"工具执行失败：{exc}"
```

这段 `try` 同时管着解析参数和执行工具两件事。我当初只包了后一件。

看着挺合理，跑十几次也都正常，直到某天模型给的参数少了个引号：

```
{"text": "手写循环其实不复杂。}
```

`json.loads` 当场抛 `JSONDecodeError`，它在 `try` 外面，没人接，程序直接退出——前面聊的十几轮一起没了。把解析那句挪进来就好了。异常被接住，回填给模型的是"工具执行失败：Unterminated string starting at..."，它下一轮看到这句，会自己把参数补齐重写一遍，接着往下干。

第二个坑更冤。模型说要调 `read_file`，可你的 `REGISTRY` 里只有 `get_current_time` 和 `count_text`，`REGISTRY["read_file"]` 直接抛 `KeyError`，一样能把程序带崩。所以前面才有那句 `if name not in REGISTRY`——先拦下来，回一句"没有名为 read_file 的工具。可用工具：get_current_time, count_text"。别小看后半句，模型全靠它才知道该改成什么。

这两个坑踩完，再看这段代码就顺了，异常不是要处理掉的麻烦，它是下一轮的输入。

## 4.2 手写版漏了七件事

上面那 60 行能跑通，甚至能跑得不错。但它是一个"演示级"的循环，演示的时候什么都不缺，真干起活来问题就全冒出来了。

![手写循环漏掉的七件事](assets/ch04/seven-gaps.png)

我们一个一个地来看。

先说并发执行。模型一次要求读三个文件，手写版是一个 `for` 循环串下去的，读一个等一个。三个文件本来能同时读，硬生生拖成三倍等待——省掉的那三分之二时间，就这么扔了。当然前提是判断得出"这三个工具互不依赖"，读三个不同文件算是最典型的情况。

再说错误回灌。上面我们做了 `try/except`，但只做了最粗的一层，也就是接住、转成字符串、塞回去。工具超时怎么办？返回值几十兆怎么办？错误信息太长要不要截断？这些手写版一概不管，而真实工具恰恰总在这上面出问题。

迭代上限也有讲究。手写版撞上 `max_iters`，只是丢回一句"任务没完成"，用户在界面上看见这句，完全不知道发生了什么。框架不这么干——它会先进入一段宽限期，注入提示、让模型把手头的信息总结出来，实在不行才收场（4.5 节细说）。

上下文压缩就更别提了。Agent 一直调工具，`messages` 只会越来越长，手写版一点压缩都没有。聊到第四十轮，它超出模型窗口，请求直接报 400。

还有权限拦截。手写版是模型说要调什么就调什么，哪次它心血来潮要跑 `rm -rf ./src`，你连插手的机会都没有。

中断也一样。用户想停，手写版没有入口。循环跑到一半被 `Ctrl+C` 打断，`messages` 就停在一个"有调用没结果"的非法状态，下次恢复还得先修它。

最后是结构化输出。你需要一个 JSON，它回你一段"好的，我这就为您整理"。总得有个机制逼它按 schema 交作业，还要在格式不对时自动重试。

上面这些问题，就是后面几章的主线。先不要急，我们才刚把后盖拧开，里面还有好几层。

> 现在你已经看见机芯长什么样了~~

## 4.3 换成 Agent：同样的任务

刚才那 60 行露出了七个缺口，不过这些先放着，后面几章一个个补。这一节我们换个写法，同一个任务，交给 AgentScope 的 `Agent`，看框架接管以后，代码长什么样。

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

`build_model()` 和 `build_toolkit()` 是照抄第 3 章的写法，字面意义上的照抄，两个函数原样搬过来。真正复用的只有那两个工具函数，`from bare_loop import get_current_time, count_text`，同一份实现，两边跑出来的东西才有可比性。

代码里还冒出三样新面孔，这里先打个照面，细节都在后面：

- `ReActConfig(max_iters=10)`，就是手写版那个 `max_iters` 参数，搬进了一个配置对象，4.5 节细讲；
- `PermissionMode.BYPASS`，命令行没有界面，工具默认会停下来"等人点头"，这里先整体放行，第 7 章换成真正的确认流程；
- `reply_stream(...)`，第 2 章见过的流式接口，这次用它把循环的每一拍吐成事件。

跑起来就知道，`for i in range(...)` 那行没了。

循环被 `Agent` 收进去了。你既看不到圈数，也看不到 `messages.append(...)`，连现在转到第几轮都得自己数。

不过循环也没真的躲起来，只是换了个地方，就在你写的那行 `async for` 里。

手写版的 `for` 是用来数圈数的，转一次问一次模型。这里的 `for` 改成收事件了。模型每问一次、每调一个工具、每说一句话，`Agent` 都会往外扔一个事件，你在循环体里接住就行。`MODEL_CALL_START` 表示又去问模型了，`TOOL_CALL_START` 表示模型点名要某个工具，`REPLY_END` 表示这一轮说完了。

接下来要做的，就是把这些事件按时间排开，看清一轮对话里到底发生了多少件事。4.4 节那张时间线，就是从这儿来的。

![ReAct 循环的决策点](assets/ch04/react-loop.png)

每一轮，`Agent` 在一个统一的决策点检查当前状态，然后选一条路走：

| 分支 | 触发条件 | 产出 |
|---|---|---|
| 推理 | 需要问模型 | `MODEL_CALL_START` 及后续内容块事件 |
| 行动 | 模型要求调用工具 | `TOOL_CALL_*` / `TOOL_RESULT_*` |
| 宽限 | 撞上 `max_iters`，但宽限轮数还没烧完 | 注入 `HINT_BLOCK`，逼模型产结构化结果 |
| 退出 | 模型不再要工具，或宽限期也烧完了 | `REPLY_END` |

看到这儿，回头再看第 2 章那张事件表，很多名字就都有因果解释了。要是已经忘了那张表长什么样，这里再列一遍，省得来回翻。

| 分组 | 事件 | 什么时候出现 |
|---|---|---|
| 一轮回复 | `REPLY_START` / `REPLY_END` | 整轮开始和结束，像一对括号 |
| 调用模型 | `MODEL_CALL_START` / `MODEL_CALL_END` | 每次向模型发请求；一轮里可能调用多次 |
| 文本 | `TEXT_BLOCK_START` / `_DELTA` / `_END` | 模型开始说话、逐字吐字、说完 |
| 思考 | `THINKING_BLOCK_START` / `_DELTA` / `_END` | 同上，只是内容是推理过程 |
| 工具调用 | `TOOL_CALL_START` / `_DELTA` / `_END` | 模型决定调工具，参数一点点拼出来 |
| 工具结果 | `TOOL_RESULT_START` / `_DELTA` / `_END` | 工具执行，结果一点点回传 |
| 提示 | `HINT_BLOCK` | 框架自己塞进去的提示 |
| 人机交互 | `REQUIRE_USER_CONFIRM` / `USER_CONFIRM_RESULT` | 等用户点头（第 7 章） |
| 异常 | `EXCEED_MAX_ITERS` | 迭代次数超限，被强制终止 |

表里只留了跟循环有关的几组，完整 28 种还在第 2 章 2.4 节。

对着表看，`MODEL_CALL_START` 为什么会出现第二次、第三次，就说得通了——循环转了不止一圈，每转一圈就要问一次模型。同理，`TOOL_CALL_START` 之所以能连着出现两三次，是因为模型一次就点了两三个工具。

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

`DEPTH` 决定缩进，`IGNORE_EVENTS` 决定哪些不上屏，`payload()` 负责从事件里挑出值得看的那一两个字段（工具名、token 数、结束原因）。**没有一行是装饰**——第 12 章的前端，就是把这三样东西换成 DOM 节点。

上面为了看清主干，省掉了两段，一段累计 `MODEL_CALL_START` / `TOOL_CALL_START` 的个数和 token，用来打最后那行汇总；一段把 `TEXT_BLOCK_DELTA` 攒起来，在 `MODEL_CALL_END` 时整段印出模型这一轮说的话。完整版在 `code/ch04/main.py`，跑起来就能看到它们的效果。

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

对照 4.1 手写版的输出，两边的结构完全一样，都是"模型调用 2 次、工具调用 2 次"。区别只在于手写版只打印了 `[第 N 轮]` 两行，而这里把每一拍都摊开了。

> 上面的耗时和 token 数会因模型、网络而异；**结构**才是要盯的地方。打印器只留有信息量的几类事件，`TEXT_BLOCK_START`、`TOOL_CALL_END` 这类空壳和 `HINT_BLOCK` 这类框架注入的提示都不上时间线。

这里有个地方挺反直觉——**`MODEL_CALL_END` 比 `TOOL_RESULT_END` 早**。

原因是模型一边生成，一边就把"我要调哪两个工具"声明出来了（`TOOL_CALL_START` 在流里发出），等这段生成彻底结束，才发 `MODEL_CALL_END`；而工具是拿到完整参数之后才真正执行的，结果自然排在后面。

顺带说一下缩进。`TOOL_RESULT_END` 缩进两级，和 `TOOL_CALL_START` 平级，但它其实发生在 `MODEL_CALL_END` 之后。`DEPTH` 表编码的是"这条事件属于哪一轮决策"，不是"它在时间上嵌在谁里面"。读的时候把它当成**归类**，别当成括号。按行号看时间顺序，按缩进看归属，两件事分开看就不会晕。

看这张时间线，能读出三件事：

**第一，模型调用是瓶颈。** 两次调用加起来约 3.5 秒，占了总耗时 3.7 秒的绝大部分；工具执行总共只花了 163 毫秒。这解释了为什么第 8 章的上下文压缩那么重要——省 token 就是省钱，省钱就是省时间。

**第二，工具很快，快到看不出它俩是不是并发的。**

这一点得诚实交代。有人看这张图会说"你看两个 `TOOL_CALL_START` 挨得那么近，肯定是并发"。但这个结论，这张图证明不了。`get_current_time` 和 `count_text` 都是瞬时返回的函数，串行跑也就是一两毫秒的差别，全被网络抖动盖住了。两条 `TOOL_RESULT_END` 都落在 1980ms，既可能是并发，也可能只是都太快。

真想验证，做个实验——给两个工具各加一句 `await asyncio.sleep(0.5)`。如果两条 `TOOL_RESULT_END` 还落在同一毫秒，那是真并发；如果差了半秒，就是排队。这类"改一个变量、看输出怎么变"的习惯，比记住结论有用得多。

**第三，`REPLY_END` 的 `finished_reason` 是 `completed`。**

如果它变成 `exceed_max_iters`，说明模型转晕了；变成 `error`，说明某一步炸了。**前端要按这个字段给用户不同的说法**——别让用户对着一个转圈动画等到天荒地老。

这个打印器其实就是第 12 章前端的原型。前端做的事，就是把这张时间线画成界面——把 `MODEL_CALL_START` 变成"正在输入"，把 `TOOL_CALL_START` 变成一张卡片，把 `TEXT_BLOCK_DELTA` 变成逐字打印。

## 4.5 控制循环：`ReActConfig`

手写版里，圈数是你在 `for i in range(...)` 里自己数的。换成框架，这件事交给一个配置对象——你只需要告诉它"最多转几圈"，剩下的它自己盯着。

```python
from agentscope.agent import ReActConfig

react_config = ReActConfig(
    max_iters=10,              # 最多转 10 轮
    stop_on_reject=False,      # 工具被拒绝时不终止
    structured_output_grace_iters=5,
)
```

`ReActConfig` 一共五个字段，日常用得到的主要是前三个：

| 参数 | 默认值 | 作用 |
|---|---|---|
| `max_iters` | 50 | 最多多少轮推理-行动迭代 |
| `structured_output_grace_iters` | 5 | 超过 `max_iters` 后额外允许的轮数，专门用来产出结构化输出 |
| `stop_on_reject` | `False` | 工具调用被拒绝时，是否立刻终止回复 |
| `interruption_message` | `'I notice the interruption. How can I help you?'` | 被中断时补的那句话 |
| `interruption_raise_cancelled_error` | `False` | 中断时是否抛 `CancelledError`，而不是正常结束 |

`max_iters` 默认 50，这个值比大多数人的直觉大得多。为什么？因为复杂任务确实需要转很多圈，读十个文件、改五处代码、跑三次测试，五十轮并不夸张。

但它同时也是一个成本开关。**每一次循环都是一次 API 调用**，50 轮意味着最坏情况下 50 次请求。所以示例里把它压到 10——不是因为它不够用，而是因为你要先知道自己在花多少钱。

> **撞上限之后会发生什么**
>
> 不是立刻结束。`cur_iter` 到了 `max_iters`，框架先进入**宽限期**（`structured_output_grace_iters`，默认 5 轮）。这几轮里它给模型注入一条 `<system-reminder>`，并用 `tool_choice` 把可选工具限定成结构化输出工具，逼模型把结果吐出来。
>
> 宽限期也烧完（`cur_iter >= max_iters + grace_iters`），才发 `EXCEED_MAX_ITERS` 事件、以 `finished_reason=exceed_max_iters` 收场。这个事件在 2.x 里已经标了 deprecated，只为向后兼容而保留，别拿它当主信号。
>
> 还有个更容易踩的坑——**只要结束时 `cur_iter > max_iters`，`finished_reason` 就是 `exceed_max_iters`，哪怕模型已经写完了正文。** 所以别用 `finished_reason` 判断"有没有拿到内容"，那要看 `get_text_content()`。
>
> 这么设计的好处是，"超时"不会给用户一个空白界面——宽限期里模型往往已经能把该说的说完。

## 4.6 三个常见错误

**错误 1：以为 `reply()` 只调用一次模型**

```python
reply = await agent.reply(msg)   # 这一行背后，可能是 2 次模型调用，也可能是 20 次
```

如果按"一次 reply = 一次 API 调用"来估算成本，会低估得很离谱。就拿本章这个只有两个工具的小任务来说，它已经调了 2 次。正确做法是读 `reply.usage`，或者数 `MODEL_CALL_END` 事件的个数。

**错误 2：把 `TOOL_CALL_START` 当成"工具已经执行完了"**

`TOOL_CALL_START` 只表示模型提出了申请。工具可能还在执行（`TOOL_RESULT_START`），可能被权限拦下了（`REQUIRE_USER_CONFIRM`），也可能失败了（`TOOL_RESULT_END` 且 `state="error"`）。界面上应该在这三个时刻显示不同状态。

**错误 3：在 `system_prompt` 里写"最多使用三个工具"**

模型对这类数量约束的执行力很弱，而 `max_iters` 是硬约束。**能用配置解决的，不要用提示词解决。**

## 4.7 小结

- 一般情况下，循环在**模型不再请求工具**时结束，而不是"任务完成"；撞上限、中断、异常是另外三条终止路径
- 手写循环 60 行就能跑，但漏了七件事，分别是并发、错误回灌、上限、压缩、权限、中断、结构化输出
- `Agent` 把循环藏了起来，用**事件流**暴露过程
- 事件时间线是前端界面的原型——`MODEL_CALL_START` 变成"正在输入"，`TOOL_CALL_START` 变成工具卡片
- `ReActConfig.max_iters` 默认 50，是成本开关，也是安全阀

**下一章要解决的问题**：第 4.1 节的裸循环有一个不易察觉的隐患——工具的工作目录，就是你的家目录。模型说要读 `~/Documents/工资条.xlsx`，它就去读；说要往 `~/Desktop` 写文件，它就写。**它没有"工作边界"这个概念**。本章两个工具只查时间和数字符，看不出问题；等你给它一把 `Read`、一个 `Bash`，这事儿就该出问题了。
