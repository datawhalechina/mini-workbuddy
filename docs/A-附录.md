# 附录 A　环境与模型接入速查

> 这一页是用来照抄的，不需要通读。出错时先来这里对一遍。

## A.1 安装

```bash
# 推荐用 uv，装得快、依赖解析稳
uv venv
uv pip install "agentscope>=2,<3" fastapi uvicorn python-dotenv

# 用 pip 也一样
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install "agentscope>=2,<3" fastapi uvicorn python-dotenv
```

国内网络慢的话加镜像：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "agentscope>=2,<3" fastapi uvicorn python-dotenv
```

验证：

```bash
python -c "import agentscope; print(agentscope.__version__)"
```

## A.2 三个环境变量

本书所有章节只认这三个名字。放在 `code/.env` 里，`load_dotenv()` 会自动读取。

```bash
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://your-endpoint/v1
OPENAI_MODEL=your-model-name
```

`OPENAI_BASE_URL` 为空时会连官方地址。本地部署（vLLM、Ollama、GPUStack）一定要填，否则请求会发到公网上去。

`.env` 必须进 `.gitignore`。这一条在第 14.4 节的清单里排第一，因为它出事之后没法撤回。

## A.3 常见兼容端点

只要服务商支持 OpenAI 的 `/chat/completions` 协议，就能直接用 `OpenAIChatModel`。填的是 `base_url`，注意结尾的 `/v1` 要不要带——各家不一样，填错了表现是 404。

| 服务 | base_url 形状 | 备注 |
|---|---|---|
| OpenAI 官方 | `https://api.openai.com/v1` | 留空也行 |
| DeepSeek | `https://api.deepseek.com/v1` | 兼容度高 |
| 阿里百炼（DashScope） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 必须走 compatible-mode 这个路径 |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | 版本号是 v4，不是 v1 |
| Moonshot | `https://api.moonshot.cn/v1` | |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 版本号 v3 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | 聚合了多家开源模型 |
| 本地 vLLM | `http://127.0.0.1:8000/v1` | |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | |
| GPUStack | `http://<内网IP>:<端口>/v1` | 部署在哪儿就填哪儿 |

不确定的时候，先用 curl 试一次，比在代码里猜快：

```bash
curl https://your-endpoint/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

能列出模型清单，说明地址和 key 都对了。

## A.4 常见报错对照

| 报错 | 原因 | 处理 |
|---|---|---|
| `401 AuthenticationError`，报错里的 key 尾号不是你写的那个 | 系统环境里已经有 `OPENAI_API_KEY`，而 `load_dotenv()` 默认**不覆盖**已存在的变量 | 用 `load_dotenv(override=True)`（本书所有脚本都是），或先清掉系统变量 |
| `401 AuthenticationError`，尾号对得上 | key 过期或被禁用 | 去服务商后台重新生成 |
| `404 Not Found` | `base_url` 少了或多了 `/v1` | 对照 A.3 的表 |
| `404 model not found` | 模型名不对 | 用 A.3 的 curl 列出可用模型名，照抄 |
| `Error: file_path must be an absolute path` | `Write` 只接受绝对路径 | 提示词里直接给出沙箱的绝对路径，不要写 `./sandbox` |
| `TypeError: 'async_generator' object is not iterable` | 对异步生成器用了 `enumerate` | 自己维护计数器，见第 2 章 |
| `400 context length exceeded` | 上下文超了 | 换更大窗口的模型，或缩短单次输入 |
| `ConnectError` / `Timeout` | 网络或代理 | 见下一条 |
| 请求全部 502，但浏览器能打开网页 | 系统代理拦截了 Python | 见下一条 |
| `TypeError: object AsyncGenerator can't be used in await` | 把流式响应当协程等了 | 模型调用返回的是异步生成器，不要 `await` |
| 偶发 `httpx.ReadError`，重跑一次又好了 | 服务端把连接掐了（内网网关、长连接超时都可能导致） | 见"连接断了怎么办" |

**代理问题值得单独说。** 环境里设了 `http_proxy` 时，`httpx` 默认会走代理，本地地址（`127.0.0.1`、内网 IP）的请求也会被一起代理出去，表现为 502 或超时。两个办法：

```bash
# 办法一：给本地地址开例外
export NO_PROXY=127.0.0.1,localhost,192.168.0.0/16
```

```python
# 办法二：让这个模型的 httpx 客户端彻底不看环境变量
import httpx

model = OpenAIChatModel(
    credential=OpenAICredential(api_key=API_KEY, base_url=BASE_URL or None),
    model=MODEL_NAME,
    context_size=32_768,
    client_kwargs={"http_client": httpx.AsyncClient(trust_env=False)},
)
```

`client_kwargs` 会原样传给 `openai.AsyncClient`，`trust_env=False` 让它忽略 `http_proxy` / `https_proxy` / `no_proxy` 三个环境变量。内网端点用这个最省事。

**连接断了怎么办。** 内网端点偶尔会在流式中途断连，报 `httpx.ReadError`。这不是代码错误，但会中断整轮对话，而且**默认不会重试**：

- 模型层 `OpenAIChatModel(max_retries=3)` 只覆盖建立连接和普通 API 错误
- Agent 层的 `ModelConfig.max_retries` 默认是 **0**，一次失败直接向外抛

要让它自己扛住，给 Agent 配一份 `ModelConfig`：

```python
from agentscope.agent import ModelConfig

agent = Agent(
    ...,
    model_config=ModelConfig(max_retries=1),
)
```

`ModelConfig` 只有两个字段：`max_retries` 和 `fallback_model`。后者可以挂一个备用模型，主模型彻底失败时切过去——内网只有一个端点的话用不上。

批处理、长任务、自检脚本都建议加上。交互式对话里加不加都行——用户看得见错误，按一下重发比干等两分钟好。

写测试脚本时如果用的是标准库 `urllib`，要用一个空代理的 opener：

```python
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
```

## A.5 算自己的成本

不要相信任何"某模型多便宜"的结论，价格变动频繁。用第 13 章的公式，把当前单价填进去自己算：

```
单次回复成本 = 输入 tokens / 1e6 × 输入单价 + 输出 tokens / 1e6 × 输出单价
```

要知道的两件事：

- **输入 token 数随轮次线性增长。** 第 9 章之后每轮都带完整上下文，第 10 轮的单次成本大约是第 1 轮的好几倍
- **缓存输入的价格通常是正常输入的一到两成。** 同一个会话连续聊，从第二轮开始普遍有缓存命中；换话题重新开头，命中率会掉下来

所以"一次对话多少钱"这个问题没有统一答案，只有"你的使用方式下多少钱"。第 13 章的面板就是为回答这个问题做的。

## 附录 B　事件类型全表

28 个事件类型。第二列是"什么时候发"，第四列是"前端该怎么做"——照第四列写 reducer，基本不会漏。

字段列只列框架自带的业务字段，`id / created_at / metadata / type` 这四个每个事件都有，不再重复。

### B.1 回复的边界

| 类型 | 何时发出 | 业务字段 | 前端 |
|---|---|---|---|
| `REPLY_START` | 一轮回复开始 | `session_id, reply_id, name, role` | 新建一个助手气泡，记下 `reply_id` |
| `REPLY_END` | 一轮回复结束（正常、被中断或步数用尽） | `session_id, reply_id, finished_reason, error` | 解锁输入框；`interrupted` 插提示；`exceed_max_iters` 补一句"这一轮步数用完了"；`error` 非空时显示错误 |

### B.2 模型调用

| 类型 | 何时发出 | 业务字段 | 前端 |
|---|---|---|---|
| `MODEL_CALL_START` | 每次调用模型前 | `reply_id, model_name` | 用于计时和显示模型名（**模型名只有这里有**） |
| `MODEL_CALL_END` | 模型返回完毕 | `reply_id, input_tokens, output_tokens, cache_input_tokens, cache_creation_input_tokens, finished_reason` | 累加 token 与费用；这是记账的唯一数据源 |

### B.3 文本、思考、数据

| 类型 | 何时发出 | 业务字段 | 前端 |
|---|---|---|---|
| `TEXT_BLOCK_START` | 一段正文开始 | `reply_id, block_id` | 建一个文本块 |
| `TEXT_BLOCK_DELTA` | 正文增量 | `reply_id, block_id, delta` | 追加 `delta`，只改 `textContent` |
| `TEXT_BLOCK_END` | 一段正文结束 | `reply_id, block_id, text` | 可用 `text` 校准（防丢帧） |
| `THINKING_BLOCK_START` | 思考开始 | `reply_id, block_id` | 建一个默认折叠的思考块 |
| `THINKING_BLOCK_DELTA` | 思考增量 | `reply_id, block_id, delta` | 追加 |
| `THINKING_BLOCK_END` | 思考结束 | `reply_id, block_id` | — |
| `DATA_BLOCK_START` | 多模态数据开始 | `reply_id, block_id, media_type, name` | 按 `media_type` 建图片/音频容器 |
| `DATA_BLOCK_DELTA` | 数据增量 | `reply_id, block_id, media_type, data, url` | 追加或直接用 `url` |
| `DATA_BLOCK_END` | 数据结束 | `reply_id, block_id` | — |

### B.4 工具

| 类型 | 何时发出 | 业务字段 | 前端 |
|---|---|---|---|
| `TOOL_CALL_START` | 模型决定调用工具 | `reply_id, tool_call_id, tool_call_name` | 建工具卡片。**这里没有参数** |
| `TOOL_CALL_DELTA` | 参数增量 | `reply_id, tool_call_id, delta` | 累加字符串，拼完再 `JSON.parse` |
| `TOOL_CALL_END` | 参数发完 | `reply_id, tool_call_id` | 此时才可以解析参数 |
| `TOOL_RESULT_START` | 工具开始执行 | `reply_id, tool_call_id, tool_call_name` | 标记运行中。**确认续跑时只有这个，没有 `TOOL_CALL_START`** |
| `TOOL_RESULT_TEXT_DELTA` | 工具返回正文增量 | `reply_id, tool_call_id, delta` | 追加到卡片的输出区 |
| `TOOL_RESULT_DATA_DELTA` | 工具返回多模态数据 | `reply_id, tool_call_id, block_id, media_type, data, url` | 按类型渲染 |
| `TOOL_RESULT_END` | 工具执行完毕 | `reply_id, tool_call_id, state` | `state == "error"` 时卡片变红，流程继续 |

### B.5 交互与异常

| 类型 | 何时发出 | 业务字段 | 前端 |
|---|---|---|---|
| `REQUIRE_USER_CONFIRM` | 权限引擎要人拍板 | `reply_id, tool_calls` | 弹确认框；`tool_calls[i].input` 是 JSON 字符串 |
| `REQUIRE_EXTERNAL_EXECUTION` | 工具要交给外部系统执行 | `reply_id, tool_calls` | 弹"由外部执行"面板 |
| `USER_CONFIRM_RESULT` | 用户回执（入参，一般不转发） | `reply_id, confirm_results` | — |
| `USER_INTERRUPT` | 用户中断（入参，一般不转发） | `reply_id` | — |
| `EXTERNAL_EXECUTION_RESULT` | 外部执行结果（入参） | `reply_id, execution_results` | — |
| `EXCEED_MAX_ITERS` | 达到 ReAct 迭代上限 | `reply_id, name` | 提示"这一轮步数用完了" |
| `HINT_BLOCK` | 注入块的提示信息 | `reply_id, block_id, source, hint` | 调试面板里显示，不要放进正文 |
| `CUSTOM` | 用户自定义 | `name, value` | 按自己的约定处理 |

### B.6 三个最容易记错的点

**`TOOL_CALL_START` 没有 `input`。** 参数在后续的 `TOOL_CALL_DELTA` 里，一字一句拼出来。工具卡片上那个"参数是流式的"的经验，就来自这个字段缺失。

**`MODEL_CALL_END` 没有 `model_name`。** 它在配对的 `MODEL_CALL_START` 上，记账时要提前存。

**`REPLY_END` 有四种结束方式。** `finished_reason` 是 `ReplyFinishedReason` 枚举，取值 `completed` / `interrupted` / `exceed_max_iters` / `error`；另一个 `error` 字段（`ErrorInfo`）在失败时非空。两个要分开看——`exceed_max_iters` 时 `error` 是空的，光看它判不出来。前端四种都要给用户不同的说法：示例代码里 `REPLY_END` 一到就无条件解锁输入框，所以不会锁死，但如果只认 `completed`，用户就看不出"被中断"和"步数用尽"的区别。

查字段比读文档快：

```bash
python -c "from agentscope.event import ToolCallStartEvent as E; print(E.model_fields.keys())"
```

## 附录 C　章节索引

### C.1 章 × 代码 × 新概念

| 章 | 代码目录 | 新增概念 | 关键 API |
|---|---|---|---|
| 1 | `code/ch01` | Agent = Model + Loop + Tools | `Agent`, `reply_stream` |
| 2 | `code/ch02` | 消息与内容块 | `UserMsg`, `TextBlock`, `EventType` |
| 3 | `code/ch03` | 工具与工具箱 | `ToolBase`, `Toolkit`, `input_schema` |
| 4 | `code/ch04` | ReAct 循环 | `ReActConfig`, `max_iters` |
| 5 | `code/ch05` | 工作区与路径边界 | `LocalWorkspace`, 先读后写 |
| 6 | `code/ch06` | 自定义工具 | `ToolBase.call`, 错误返回 |
| 7 | `code/ch07` | 权限系统 | `PermissionMode`, `PermissionRule`, `PermissionEngine` |
| 8 | `code/ch08` | 给选项与拍板 | `structured_schema`, `ActionPlan` |
| 9 | `code/ch09` | 状态持久化 | `AgentState`, `model_dump_json` |
| 10 | `code/ch10` | 后端与事件流 | `FastAPI`, `StreamingResponse`, SSE 帧 |
| 11 | `code/ch11` | 前端渲染 | reducer, `ReadableStream`, 增量拼接 |
| 12 | `code/ch12` | 确认、提问、中断 | `UserConfirmResultEvent`, `UserInterruptEvent` |
| 13 | `code/ch13` | 记账与可观测 | `MODEL_CALL_END` 字段, JSONL |
| 14 | `code/ch14` | 交付与自检 | `create_app`, `GoalPipeline`, `MCPClient` |

### C.2 想改某个东西，去哪个文件

| 你想改 | 去这里 |
|---|---|
| 换模型 / 换服务商 | `build_model()`，每章一份，改一处 |
| 加一个工具 | `code/ch06/main.py` 里按 `ToolBase` 三件套写：`name / description / input_schema` + `call()` |
| 改权限判定 | `code/ch07/main.py` 的规则配置 |
| 加一个接口 | `code/ch12/server.py`，注意插在静态目录之前 |
| 改界面布局 | `code/ch13/index.html` + `style.css` |
| 改事件渲染 | `code/ch13/app.js` 的 `handle()` switch |
| 加一项统计 | `code/ch13/telemetry.py` 的 `Recorder.observe` |

### C.3 三种运行模式

| 模式 | 命令 | 需要 key | 用途 |
|---|---|---|---|
| 纯前端 | 双击 `code/ch11/mock.html` | 否 | 调界面、截图、给人看效果 |
| 假模型 | `MOCK=1 python main.py` | 否 | 调后端、调事件流、跑自检 |
| 真模型 | 填好 `.env` 后 `python main.py` | 是 | 实际使用 |

