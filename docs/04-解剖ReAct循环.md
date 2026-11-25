# 第 4 章　解剖 ReAct 循环

> 本章代码 `code/ch04/bare_loop.py`、`code/ch04/main.py`　·　读完约 30 分钟

## 4.0 上一章留下的问题

第 3 章 3.1.1 的步骤表，最后一行写着"模型若还要调工具，就回到第 2 步"。

当时一句话带过，因为答案要留到这一章。现在正面回答三个问题：

1. 一次任务到底要转几轮？
2. 什么时候停？
3. 停不下来怎么办？

这三个问题的答案，构成 Agent 的机芯。后面几章都是给这块机芯装表壳、上表盘：权限、持久化、前端，以及 4.2 列出的七个缺口。

准备好了吗？把后盖拧开，看看里面有几个齿轮。

## 4.1 先手写：不用框架，看清循环

先不用框架，手写一遍。这段代码没有 `import agentscope`，直接调 OpenAI SDK，把上一章那张往返图跑起来。

> 学开车先摸手动挡：离合什么时候踩、挡什么时候换，脚上有感觉了，再开自动挡才知道变速箱在替你做什么。

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

为了看清主干，上面的代码删掉了部分注释和换行。完整文件在 `code/ch04/bare_loop.py`，含工具定义一共 153 行，其中 `bare_loop` 函数本身 60 行。

`cd code/ch04 && python bare_loop.py` 就能跑。它每轮打印模型返回了几个 `tool_calls`，这个数字就是循环的节拍。

```
>>> 现在几点？顺便数一下这句话有多少字：手写循环其实不复杂。

  [第 1 轮] 模型返回 tool_calls=2
           → 调用 get_current_time({})
           → 调用 count_text({'text': '手写循环其实不复杂。'})
  [第 2 轮] 模型返回 tool_calls=0
```

两行 `[第 N 轮]`，说明循环转了两圈。第一圈模型要了两个工具，第二圈不再要工具，`if not choice.tool_calls` 命中，`return` 结束。

回到 4.0 的第一个问题：一次任务转几轮？圈数不固定，正常结束时等于模型递交申请单的轮数加一，最后一圈是来交答案的。这个任务要了两个工具，但两张申请单在同一圈里，所以总共两圈。如果模型先查时间、看到结果再决定数字符，就得三圈。

循环本身没有神秘之处：问模型、执行工具、把结果递回去，直到它不再要工具。

> 模型最后的措辞每次都会变，但 `[第 1 轮] tool_calls=2` → `[第 2 轮] tool_calls=0` 这个骨架不会变。看输出盯骨架，别盯模型说了什么。
>
> 真正容易写错的不是"转圈"，而是圈里的四件事：凭什么停（4.1.1）、申请单为什么要存回去（4.1.2）、工具结果怎么配对（4.1.3）、工具炸了怎么办（4.1.4）。

### 4.1.1 循环什么时候停

先说第一件，也是最容易被忽略的：循环凭什么停？

很多人以为，循环会一直转到任务做完为止，做完自然就停。

这话大体没错，但"做没做完"只有模型自己知道。你手上能看见的信号只有一个：这次响应里有没有 `tool_calls`。没有，说明模型认为信息够了，愿意直接回答；有，说明它还想要更多。

这条判断成立的前提，是模型愿意主动要工具。它什么时候愿意？多半取决于工具描述写得清不清楚：描述到位，它一眼能对上"这个问题该用哪个工具"；描述含糊，它宁愿凭记忆硬答，也不愿调用一个自己看不懂的函数。这又回到第 3 章的话题。

以上是正常情况。还有三种情况会让循环结束：撞上 `max_iters`、用户中断、异常没接住。前两种是正常收场，前提是代码留了入口；第三种是进程直接崩掉，连收场都谈不上（4.5 节讲上限，第 12 章讲中断）。

还有个坑藏得比较深。

模型的输出有长度上限（请求里的 `max_tokens`，或模型自身的能力上限）。一旦撞上，话说到一半被掐断，`finish_reason` 会变成 `length` 而不是 `stop`，意思是，我没说完。

掐在哪儿，后果不一样：

- **掐在正文中间**，一个 `tool_calls` 都没有，循环按正常结束处理，`return choice.content` 交出去的是一句半截话，比如"现在的北京时间是 2026-10-30 07:5"。它不报错，这句话就这么显示给用户了。
- **掐在工具参数里**，模型正在写 `{"text": "手写循环其实不复`，参数 JSON 还没闭合就被掐断。这时 `tool_calls` 是有的，但 `json.loads` 拿到的是半截字符串，直接抛 `JSONDecodeError`。这正是 4.1.4 的 `try` 要把参数解析一起包住的原因。

所以只盯 `tool_calls` 不够，`finish_reason` 也得看一眼：`stop` 是正常说完，`tool_calls` 是要调工具，`length` 是被截断，`content_filter` 是被安全策略拦下。前两种可以往下走，后两种要按异常处理。

上面的代码没有这个检查，只留了 `if not choice.tool_calls` 一道判断。不是不需要，是为了不岔开主干。要补的话，大概是这样：

```python
if resp.choices[0].finish_reason == "length":
    return "（回答被截断，可以把 max_tokens 调大后重试）"
```

用框架时，这类截断由框架统一处理，一般不用自己写。

### 4.1.2 每次工具调用都要原样存回历史

4.1.1 讲的是循环什么时候结束，这一节讲每一轮留下了什么。

先明确："历史"就是 `messages` 这个列表。每次调用模型，都把它整个发过去。模型之所以"记得"前几轮聊过什么，不是它自带记忆，而是你每次都把这份记录重新递了一遍。

模型这一轮的回复不只有一句话，还可能附带一张工具申请单，也就是 `tool_calls`，写明要调哪几个函数、参数填什么。这张单子必须和文字一起，作为一条 `assistant` 消息存进 `messages`：

```python
messages.append({
    "role": "assistant",
    "content": choice.content,                                     # 它说的话
    "tool_calls": [tc.model_dump() for tc in choice.tool_calls],    # 它开的申请单
})
```

> `tc.model_dump()` 只是把 SDK 对象转成普通 dict，方便原样塞回请求。手写也行，但 `id`、`type`、`function` 里的 `name` 和 `arguments` 一个都不能丢，下一小节配对靠的就是 `id`。

这一步很容易漏。漏了会怎样？把 `messages` 打印出来最直观。

存对了，历史是一条申请单紧跟一条处理结果：

```json
[
  {"role": "user", "content": "现在几点？"},
  {
    "role": "assistant",
    "content": "",
    "tool_calls": [
      {
        "id": "call_abc",
        "type": "function",
        "function": {"name": "get_current_time", "arguments": "{}"}
      }
    ]
  },
  {"role": "tool", "tool_call_id": "call_abc", "content": "2026-10-30 15:57:21"}
]
```

漏了那条 `assistant` 呢？历史里只剩一条 `user` 和一条 `tool`。`tool` 消息在说"`call_abc` 这事办完了"，可整段历史里找不到 `call_abc` 这张单子。API 校验时直接返回 400，报错大意是 `messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。

所以申请单必须留着：它不只是给模型看的上下文，还是那条 `tool` 消息的"出身证明"。

### 4.1.3 工具结果要配对回填

申请单存好了，结果也回来了，现在把结果写回历史。关键是让每条结果都带上"我回答的是哪张单子"。编号就是申请单里的 `tc.id`，填进 `tool_call_id`：

```python
# tc 就是当前这张申请单，tc.id 就是它的编号
messages.append({
    "role": "tool",
    "tool_call_id": tc.id,     # 回答的是哪一张单子
    "content": str(result),    # 办完的结果；API 只收字符串，所以要转一下
})
```

对照 4.1.2 那份历史，`assistant` 里的 `"id": "call_abc"` 和 `tool` 里的 `"tool_call_id": "call_abc"` 是同一个值。这就是配对的全部含义：API 靠编号把结果认领回具体的申请单，不看它排在第几个。

规则是：有一张申请单，就回一条结果。模型一次要调三个工具，就回三条 `tool` 消息，各自带好自己的 `tool_call_id`；少一条，API 就报错（大意是每个 `tool_calls` 都要有对应的 `tool` 消息）。

顺序上建议照申请单的次序来。大多数服务按 `tool_call_id` 匹配，按顺序排不会吃亏，打乱了却可能在别的实现上出问题，没必要冒这个险。

> 编号从哪来？翻回第 2 章看 `assemble_msg.py` 的输出：`id="call_1"` 出现两次，一次在调用上，一次在结果上。

### 4.1.4 异常必须变成结果

前三节默认工具能顺利跑完。现实里还有另一半：跑不完。参数是坏的，工具名是编的，真跑起来还会炸，比如文件不在、网络断了、没权限。

这些接不住，Agent 就成了一次性的：模型错一次，会话报废。这一节要做的，是把失败也变成一条能继续往下传的消息。

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

这段 `try` 同时管着解析参数和执行工具两件事。常见写法是只包后一件。

看着合理，跑十几次也正常，直到某天模型给的参数少了个引号：

```
{"text": "手写循环其实不复杂。}
```

`json.loads` 当场抛 `JSONDecodeError`。它在 `try` 外面，没人接，程序直接退出，前面十几轮一起没了。把解析那句挪进来就好了：异常被接住，回填给模型的是"工具执行失败：Unterminated string starting at..."。它下一轮看到这句，会自己把参数补齐重写，接着往下干。

第二个坑更隐蔽。模型说要调 `read_file`，可 `REGISTRY` 里只有 `get_current_time` 和 `count_text`，`REGISTRY["read_file"]` 直接抛 `KeyError`，一样能把程序带崩。所以才有 `if name not in REGISTRY`，先拦下来，回一句"没有名为 read_file 的工具。可用工具：get_current_time, count_text"。别小看后半句，模型全靠它才知道该改成什么。

这两个坑踩完，再看这段代码就顺了：异常不是要处理掉的麻烦，而是下一轮的输入。

## 4.2 手写版漏了七件事

上面那 60 行能跑通，甚至跑得不错。但它只是演示级的循环：演示时什么都不缺，真干活问题就冒出来。

![手写循环漏掉的七件事](assets/ch04/seven-gaps.png)

一个一个看。

**并发执行。** 模型一次要求读三个文件，手写版用一个 `for` 循环串下去，读一个等一个。三个文件本可以同时读，串行却要等三轮，多等的两份时间纯属浪费。前提是能判断出这三个工具互不依赖，读三个不同文件是最典型的情况。

**错误回灌。** 上面做了 `try/except`，但只做了最粗的一层：接住、转成字符串、塞回去。工具超时怎么办？返回值几十兆怎么办？错误信息太长要不要截断？手写版一概不管，而真实工具恰恰常在这上面出问题。

**迭代上限。** 手写版撞上 `max_iters`，只丢回一句"任务没完成"，用户在界面上看到这句，完全不知道发生了什么。框架不这么干：撞上限后它还会再催一次，注入提示让模型把查到的东西总结成文字，实在不交才收场（4.5 节细说）。

**上下文压缩。** Agent 一直调工具，`messages` 只会越来越长。手写版没有压缩，聊到几十轮就可能超出模型窗口，请求直接报 400。这一项本书不展开，知道有这回事就行；真要做长对话，去查框架的上下文管理配置。

**权限拦截。** 手写版是模型说要调什么就调什么。哪次它要跑 `rm -rf ./src`，你连插手的机会都没有。

这一条值得停一下：它靠提示词解决不了，而且经常被一句话糊过去。它其实是两件事挤在一起，得拆开看。

**第一件是边界，也就是工具能不能做到。** `Read` 收到工作区外的绝对路径会照读，`Write` 收到同样的路径会照写。它没有"我该待在哪"的概念，路径对它就是个字符串，`open()` 传什么它开什么。

提示词解决不了这件事。你在 `system_prompt` 里写十遍"不要访问工作区以外的文件"，它依然可能某次推理里顺手看一眼。不是它想造反，是它根本不知道边界在哪，它没有"工作区"这个参照系，也就无从自检。更糟的是，这种"不知道边界"是工具描述带进来的，框架自带的 `Read` 第一句就是 `You can access any file directly by using this tool`，还要求参数必须是绝对路径。它根本没有"相对"这个概念，`chdir` 也管不着它。

工程上的正解叫**路径归一化**，工具拿到的每个路径都先拼回根目录、解析成绝对路径，再校验；落在根目录外的直接拒绝。

```python
ROOT = Path("./workspace").resolve()

def safe_path(p: str) -> Path:
    target = (ROOT / Path(p).expanduser()).resolve()   # ~、..、软链接全展开
    if not target.is_relative_to(ROOT):                # 绝对路径会顶掉 ROOT，靠这行拦
        raise PermissionError(f"{p} 不在工作区内")
    return target
```

`ROOT / "/etc/passwd"` 的结果就是 `/etc/passwd`，右边是绝对路径时，它直接覆盖左边。所以那行校验不是防御性冗余，是唯一的拦截点。这一层管得住走路径的读写工具，管不住 `Bash`：它收的是一个字符串，`cd /` 一句话就出去了，只能换执行环境（容器、bubblewrap 那一类）。第 5 章讲工作区，第 7 章讲审批。

**第二件才是审批，也就是要不要做。** 模型申请删三个文件，你不一定都同意；同一个写操作落在 `./tmp` 和落在 `./src`，风险也不是一回事。这是 `PermissionMode` 管的事。

两件都得有，因为它们拦的不是同一样东西，边界拦能力，审批拦意图。缺边界，一次误判就是既成事实，连补救的机会都没有；缺审批，边界之内那些"合法但不该做"的操作照样会发生。

**中断。** 用户想停，手写版没有入口。循环跑到一半被 `Ctrl+C` 打断，`messages` 就停在一个"有调用没结果"的非法状态，下次恢复还得先修它。

**结构化输出。** 你需要一个 JSON，它回你一段"好的，我这就为您整理"。总得有个机制逼它按 schema 交作业，还要在格式不对时自动重试。

这七个缺口就是后面几章的主线。后盖才刚拧开，里面还有几层。

## 4.3 换成 Agent：同样的任务

七个缺口先放着，后面几章一个个补。这一节换个写法：同一个任务交给 AgentScope 的 `Agent`，看框架接管后代码长什么样。

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

`build_model()` 和 `build_toolkit()` 复用第 3 章的写法，这里不再解释。

唯一要留意的是工具的来源。`main.py` 开头那句 `from bare_loop import count_text, get_current_time` 不是随手写的：`build_toolkit()` 里注册的就是手写版那两个函数本身，没另写一份。同一份实现、同一套行为，两边跑出来的结果才能比较。

代码里冒出三样新东西，先打个照面：

- `ReActConfig(max_iters=10)`：就是手写版的 `max_iters`，搬进了配置对象，4.5 节细讲。手写版给 5，这里写 10，只是留点余量。
- `PermissionMode.BYPASS`：命令行没有界面，工具默认会停下来等人点头，这里先整体放行，第 7 章换成真正的确认流程。
- `reply_stream(...)`：第 2 章见过的流式接口，这次用它把循环的每一拍吐成事件。

把两段代码并排看，最先注意到的是 `for i in range(...)` 那行没了。

数圈数、往 `messages` 里追加、判断要不要再问一次，原来那几十行干的事，现在一行不剩，全被 `Agent` 收进去了。想知道转了几轮，只能自己数事件。

不过循环没真躲起来，只是换了个地方，就在你写的那行 `async for` 里。

手写版的 `for` 用来数圈数，转一次问一次模型；这里的 `for` 改成收事件。模型每问一次、每调一个工具、每说一句话，`Agent` 都会往外扔一个事件，你在循环体里接住就行。`MODEL_CALL_START` 表示又去问模型了，`TOOL_CALL_START` 表示模型点名要某个工具，`REPLY_END` 表示这一轮说完了。

把这些事件按时间排开，就能看清一轮对话里到底发生了多少件事。4.4 节的时间线就是这么来的。

![ReAct 循环的决策点](assets/ch04/react-loop.png)

每一轮，`Agent` 在统一的决策点检查当前状态，然后选一条路：

| 分支 | 触发条件 | 产出 |
|---|---|---|
| 推理 | 需要问模型 | `MODEL_CALL_START` 及后续内容块事件 |
| 行动 | 模型要求调用工具 | `TOOL_CALL_*` / `TOOL_RESULT_*` |
| 宽限 | 撞上 `max_iters` | 注入 `HINT_BLOCK`，工具关掉或换成结构化输出工具，逼模型交结果 |
| 退出 | 模型不再要工具，或收尾那轮也没交出结果 | `REPLY_END` |

看到这儿，第 2 章那张事件表里的名字就都有因果解释了。跟循环直接相关的只有四组：

| 分组 | 事件 | 什么时候出现 |
|---|---|---|
| 一轮回复 | `REPLY_START` / `REPLY_END` | 整轮开始和结束，像一对括号 |
| 调用模型 | `MODEL_CALL_START` / `MODEL_CALL_END` | 每次向模型发请求；一轮里可能调用多次 |
| 工具调用 | `TOOL_CALL_START` / `TOOL_CALL_DELTA` / `TOOL_CALL_END` | 模型决定调工具，参数一点点拼出来 |
| 工具结果 | `TOOL_RESULT_START` / `TOOL_RESULT_TEXT_DELTA` / `TOOL_RESULT_DATA_DELTA` / `TOOL_RESULT_END` | 工具执行，结果一点点回传 |

加上文本、思考、数据、提示、人机交互、异常等其余分组，一共 28 种，见 2.4 节。

对着表看，`MODEL_CALL_START` 为什么会出现第二次、第三次，就说得通了，循环转了不止一圈，每转一圈就要问一次模型。同理，`TOOL_CALL_START` 能连着出现两三次，是因为模型一次递了两三张申请单，工具得一张一张办。

## 4.4 事件时间线：把循环的每一拍摊开

`code/ch04/main.py` 里有一个打印器，把事件流按"层级 + 耗时"排成时间线。看懂它要抓住三件事：哪些事件上屏、缩进从哪来、每行右边那截文字从哪来。

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

三样东西分别管什么：

`DEPTH` 管缩进。`REPLY_START` 记 0，`MODEL_CALL_START` 记 1，`TOOL_CALL_START` 记 2，`"  " * DEPTH.get(kind, 1)` 一乘，层级就出来了。表里没写的事件默认按 1 层处理，不至于因为漏登记就挤到最左边。

`IGNORE_EVENTS` 管过滤。`TOOL_CALL_END`、`TEXT_BLOCK_START` 这类事件只表示"某件事开始/结束了"，本身没有内容可看，留在时间线上就是噪音。滤掉之后，同一个工具名出现两次就是实实在在调了两次，中间不会夹着没用的行。逐字增量 `*_DELTA` 也在这一行被跳过，不然光打字就能刷满一屏。

`payload(event)` 管挑字段。一个事件带着一堆属性，屏幕上放不下也不该全放，于是工具调用只取工具名，模型调用只取 `in= / out=` 的 token 数，一轮结束只取结束原因。这个函数是几个 `if`，按事件名取对应字段，没贴进正文。

这三样东西也是第 11 章前端要的全部素材：把 `print` 换成创建 DOM 节点，`"  " * DEPTH` 换成 CSS 的 `padding-left`，`payload(event)` 换成往节点里填文本，命令行里的时间线就变成网页上的对话记录。

为了看清主干，上面省掉了三处：`payload()` 的完整实现；累计 `MODEL_CALL_START` / `TOOL_CALL_START` 的个数和 token、用来打最后一行汇总；把 `TEXT_BLOCK_DELTA` 攒起来、到 `MODEL_CALL_END` 时整段印出模型这一轮说的话。完整版都在 `code/ch04/main.py`，跑起来就能看到效果。

`cd code/ch04 && python main.py`，实测输出：

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
- "手写循环其实不复杂。"：共 10 个字符，其中汉字 9 个

    3715ms  REPLY_END              completed
------------------------------------------------------------------------
模型调用 2 次｜工具调用 2 次｜token in=946 out=273｜总耗时 3715ms
```

拿 4.1 手写版的输出对一下：骨架一模一样，都是问两次模型、调两次工具。差别在手写版只打两行 `[第 N 轮]`，这里换成了事件流。

耗时和 token 数跟模型、网络都有关系，别照数字背，盯结构就行。

有个地方第一次看容易卡住：`MODEL_CALL_END` 居然排在 `TOOL_RESULT_END` 前面。原因是模型一边生成一边就把"要调哪两个工具"说出来了，`TOOL_CALL_START` 在流里陆续发出，直到这段生成写完，才发 `MODEL_CALL_END`；工具要等参数齐了才开始跑，结果自然落在后面。

顺带说缩进。`TOOL_RESULT_END` 缩了两级，和 `TOOL_CALL_START` 平级，可它实际发生在 `MODEL_CALL_END` 之后。`DEPTH` 记的是"这条事件属于哪一轮决策"，不是"它在时间上嵌在谁里面"。读的时候当成归类，别当成括号。按左边的毫秒看时间，按缩进看归属。

这张时间线最直接的信息是时间花在哪。两次模型调用加起来 3.5 秒，总耗时 3.7 秒，工具执行一共 163 毫秒。慢的是等模型写字，不是干活。上下文越长，这一趟回得越慢，token 也越贵。

第二件事有点扫兴：这张时间线证明不了两个工具是不是并发跑的。有人会说两个 `TOOL_CALL_START` 挨得那么近，肯定是并发，可 `get_current_time` 和 `count_text` 都是瞬时返回的东西，串行跑撑死差一两毫秒，早被网络抖动盖过去了。两条 `TOOL_RESULT_END` 都停在 1980ms，既可能是并发，也可能只是都太快。

真想弄明白，改个变量试试：把两个工具改成 `async`，各加一句 `await asyncio.sleep(0.5)`，再跑一遍。两条结果还落在同一毫秒就是真并发，差了半秒就是排队。这种改一点、看输出怎么变的习惯，比记住别人给的结论管用。

最后一行 `REPLY_END` 后面跟着 `completed`，说明这轮真走完了。这个字段换了值就有事，`exceed_max_iters` 是转晕了被拦住，`error` 是中间某步炸了，`interrupted` 是用户按了停止。前端照着它给不同说法，用户才不至于对着转圈动画干等。

## 4.5 控制循环：`ReActConfig`

手写版里圈数是你自己数的，`for i in range(max_iters)` 写几就转几。现在这个数字挪到配置对象上，`ReActConfig(max_iters=10)` 里的 `max_iters` 管的还是同一件事。

不一样的只有撞限之后怎么办。手写版跑满就返回一句"没完成"；框架后面还有动作，本节末尾细说。

```python
from agentscope.agent import ReActConfig

react_config = ReActConfig(
    max_iters=10,                        # 最多转 10 轮
    structured_output_grace_iters=5,     # 结构化输出的宽限轮数，默认也是 5（见下表）
)
```

`ReActConfig` 一共五个字段，本章真正用得上的是前两个，剩下三个先摆在这儿，知道有这回事就行。`stop_on_reject` 要等第 7 章的权限机制到位才有意义，最后两个跟中断有关，第 12 章再说。

| 参数 | 默认值 | 作用 |
|---|---|---|
| `max_iters` | 50 | 最多多少轮推理-行动迭代 |
| `structured_output_grace_iters` | 5 | 超过 `max_iters` 后额外允许的轮数，专门用来产出结构化输出 |
| `stop_on_reject` | `False` | 工具调用被拒绝时，是否立刻终止回复 |
| `interruption_message` | `'I notice the interruption. How can I help you?'` | 被中断时补的那句话 |
| `interruption_raise_cancelled_error` | `False` | 中断时是否抛 `CancelledError`，而不是正常结束 |

`max_iters` 默认 50，比大多数人的直觉大得多。为什么？因为复杂任务确实需要转很多圈：读十个文件、改五处代码、跑三次测试，五十轮并不夸张。

但它同时是成本开关。每一次循环都是一次 API 调用，50 轮意味着最坏情况下 50 次请求。示例里把它压到 10，不是这个任务需要 10 圈，其实两圈就完了，写 5 还是 10 都碰不到上限，这么写是要你记住，这个数字决定最坏情况下你要为多少次请求付钱。

> **撞上 `max_iters` 之后会发生什么**
>
> 不会立刻停。框架先塞一条 `<system-reminder>`，要模型把已查到的内容整理成文字交出来，同时把 `tool_choice` 设成 `"none"`、工具全关，这就是 4.2 说的"再催一次"。这次调用也算轮数，跑完 `cur_iter` 从 10 变成 11。
>
> 如果给 agent 设了 `structured_schema`，宽限就不是一次，而是上表里的 `structured_output_grace_iters` 轮：这几轮工具不关，改用专门产出结构化结果的工具。
>
> 连收尾也没交出文字，才真收场：发 `EXCEED_MAX_ITERS`，`finished_reason` 记成 `exceed_max_iters`。这个事件在框架 2.x 已被标记为 deprecated，别拿它当主信号。
>
> 还有个坑：只要收尾调用跑过，`cur_iter` 就大于 `max_iters`，此时哪怕模型已把整段答复写完，`finished_reason` 仍记成 `exceed_max_iters`。它只回答"轮数用超没有"，不回答"东西交出来没有"，想知道后者，看最终回复的 `get_text_content()` 是否为空。

## 4.6 三个常见错误

**错误 1：以为 `reply()` 只调用一次模型**

```python
reply = await agent.reply(msg)   # 这一行背后，可能是 2 次模型调用，也可能是 20 次
```

按"一次 reply = 一次 API 调用"估算成本，会低估得很离谱。本章这个只有两个工具的小任务，已经调了 2 次模型。正确做法是读 `reply.usage`，里面的 `input_tokens` 和 `output_tokens` 是一次 reply 里所有模型调用累加出来的；也可以数 `MODEL_CALL_END` 事件的个数，两种对得上。

**错误 2：把 `TOOL_CALL_START` 当成"工具已经执行完了"**

`TOOL_CALL_START` 只表示模型提出了申请。工具可能还在执行（`TOOL_RESULT_START`），可能被权限拦下了（`REQUIRE_USER_CONFIRM`），也可能失败了（`TOOL_RESULT_END` 且 `state="error"`）。界面上应该在这三个时刻显示不同状态。

**错误 3：在 `system_prompt` 里写"最多使用三个工具"**

模型对这类数量约束的执行力很弱，而 `max_iters` 是硬约束。能用配置解决的，不要用提示词解决。

## 4.7 小结

- 一般情况下，循环在模型不再请求工具时结束，而不是"任务完成"；撞上限、用户中断是另外两条终止路径，异常没接住则是崩溃
- 手写循环 60 行就能跑，但漏了七件事：并发、错误回灌、上限、压缩、权限、中断、结构化输出。压缩不在本书范围，权限、结构化输出、中断分别在第 7、8、12 章补
- `Agent` 把循环藏了起来，用事件流暴露过程
- 事件时间线是前端界面的原型，`MODEL_CALL_START` 变成"正在输入"，`TOOL_CALL_START` 变成工具卡片
- `ReActConfig.max_iters` 默认 50，是成本开关，也是安全阀

**下一章要解决的问题**：4.2 那个 `safe_path` 能管住读写，但管不住 `Bash`，命令是个字符串，`cd /` 就出去了；而且每个工具都得自己套一层，漏一个就白搭。下一章换个做法，先划一块地，再让工具都在这块地里跑，让边界从"代码里的检查"变成"环境自带"。顺便说清一件事，`LocalWorkspace` 的 `workdir` 是锚点不是笼子，真笼子在容器那类工作区里。
