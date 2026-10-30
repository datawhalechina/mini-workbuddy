<div align="center">
    <img src="./logo.png" alt="mini-workbuddy" width="560">
</div>

<div align="center">
    📚 在线阅读：
    <a href="https://datawhalechina.github.io/mini-workbuddy/" target="_blank" style="text-decoration: none; color: #007bff; margin: 0 5px;"><strong>GitHub Pages</strong></a> 
</div>

## 🚀 前言

这个项目最初的样子，是一本 AgentScope 教程——按模块讲清每个 API 是什么、怎么调用。真开始写才发现，这样的内容在 AI 时代很难让人读完：框架文档本身已经足够完备，把问题丢给模型，得到的解释往往比教程更全。逐条讲解，无非是把文档重排一遍。

那什么才是这个项目给学习者能带来的东西？

本书仍然以理解 AgentScope 为目的，但入口不是 API，而是产品——主线定为：用 AgentScope 尽可能完整地做出一个类似 WorkBuddy 的本地助手。概念在需要它的时候出场：模型怎么接、消息长什么样、工具怎么注册、内部循环替你跑了几轮、权限和上下文在哪一层拦截、状态存在哪。每加一块能力，都先回答"为什么现在需要它"。

框架是学不完的，判断力是练出来的。这才是本项目真正想留下的东西。

## 📌 项目受众

面向会写 Python 函数和类、见过 `async/await`的读者。高校学生、想把手上的脚本做成产品的开发者、需要给团队讲清"智能体到底怎么运转"的工程师，都能从中拿到可复用的东西。

**前置知识要求：**

- 会用 Python 写函数与类，能读懂类型注解
- 知道 `async` / `await` 的作用（不要求写过多协程）
- 有大模型的基本使用经验：发过 API 请求、知道 token 是什么

## ⚙️ 运行环境

| 项 | 要求 |
|---|---|
| Python | 3.11 及以上 |
| AgentScope | **2.x** |
| 模型 | 任意兼容 OpenAI 接口的服务 |

>AgentScope 1.x 与 2.x 是两套不兼容的 API，网络上大量教程仍停留在 1.x（`ServiceToolkit`、`ReActAgent` 那一套），照抄会持续报错。本书以 2.0.5 为开发基线，个别小版本新增的能力（如 2.0.6 的 `on_check_permission` 钩子）在正文出现的那一处单独说明，各章不再重复标注版本号；完整的小版本差异见[第 15.3 节](./docs/15-毕业设计.md)。

## 📖 内容导航

五篇 15 章，每章 3000–4000 字，读完一章约 20 分钟。

>  🔄表示内容正在调整，✅表示内容已经完成

| 章节 | 关键内容 | 代码 / 文件 | 状态 |
|---|---|---|---|
| <strong>第 0 篇 · 开场：先看成品，再动手</strong> | | | |
| [第 1 章 我们究竟要造什么](./docs/01-我们究竟要造什么.md) | Agent = 模型 + 循环 + 工具；环境搭建；跑通第一条消息 | `ch01` | ✅ |
| [第 2 章 读懂一次对话](./docs/02-读懂一次对话.md) | Msg / Block / Event；`reply` 与 `reply_stream` | `ch02` | ✅ |
| <strong>第 1 篇 · 从会说话到会干活</strong> | | | |
| [第 3 章 给它一双手](./docs/03-给它一双手.md) | 工具调用、Toolkit、JSON Schema 从哪来 | `ch03` | ✅ |
| [第 4 章 解剖 ReAct 循环](./docs/04-解剖ReAct循环.md) | 裸循环与框架循环的对比；事件全景；迭代上限 | `ch04` | 🔄 |
| [第 5 章 装进一个工作区](./docs/05-装进一个工作区.md) | `LocalWorkspace`；路径边界；先读后写 | `ch05` | 🔄 |
| [第 6 章 工具不够用就自己造](./docs/06-工具不够用就自己造.md) | `ToolBase` 四件套；工具组与元工具 | `ch06` | 🔄 |
| <strong>第 2 篇 · 让它可靠</strong> | | | |
| [第 7 章 别让它乱删东西](./docs/07-别让它乱删东西.md) | 权限模式 × 规则 × 工具自检；人在环确认 | `ch07` | 🔄 |
| [第 8 章 长任务不迷路](./docs/08-长任务不迷路.md) | 任务清单；上下文压缩与卸载；注入配置 | `ch08` | 🔄 |
| [第 9 章 会话要活下去](./docs/09-会话要活下去.md) | `AgentState` 落盘与恢复；恢复时的两个陷阱 | `ch09` | 🔄 |
| [第 10 章 在关键节点插手](./docs/10-在关键节点插手.md) | 中间件洋葱；结构化输出做任务分级 | `ch10` | 🔄 |
| <strong>第 3 篇 · 做成真正可用的产品</strong> | | | |
| [第 11 章 后端把事件流出去](./docs/11-后端把事件流出去.md) | FastAPI + SSE；事件到帧的映射；断线续传 | `ch11` | 🔄 |
| [第 12 章 前端把事件拼成界面](./docs/12-前端把事件拼成界面.md) | reducer 拼装；工具时间线；流式渲染 | `ch12` | 🔄 |
| [第 13 章 交互闭环](./docs/13-交互闭环.md) | 确认、提问、中断；回执如何续跑 | `ch13` | 🔄 |
| [第 14 章 日志、成本与可观测](./docs/14-日志成本与可观测.md) | 事件流记账；成本面板；上线清单 | `ch14` | 🔄 |
| <strong>第 4 篇 · 收尾</strong> | | | |
| [第 15 章 毕业设计：从 mini 到真的](./docs/15-毕业设计.md) | 六项自检；三条升级路线；版本对照表 | `ch15` | 🔄 |
| <strong>附录</strong> | | | |
| [附录 A 环境与模型接入速查](./docs/A-附录.md) | 各家 `base_url`、常见报错对照、成本估算 | `A-附录.md` | ✅ |
| [附录 B 事件类型全表](./docs/A-附录.md#附录-b-事件类型全表) | 28 个事件：触发时机与前端渲染方式 | `A-附录.md` | ✅ |
| [附录 C 章节索引](./docs/A-附录.md#附录-c-章节索引) | 章 × 代码 × 新概念；想改某处去哪个文件 | `A-附录.md` | ✅ |

> 附录 A、B、C 是**同一个文件** `docs/A-附录.md` 里的三个 `##` 小节，不是三个文件。在线站点侧边栏已按 A / B / C 拆成三条直达链接。

## 🗂 目录结构

```
mini-workbuddy/
├── docs/                      # 教程正文，一章一个 Markdown（全部平铺）
│   ├── 01-…15-*.md
│   ├── A-附录.md              # 附录 A / B / C 三节同在此文件
│   ├── index.md               # 在线阅读站点首页
│   ├── assets/                # 配图，按章分目录
│   │   ├── chNN/*.png
│   │   └── src/*.html         # 概念图的 HTML 源文件，改字重截即可
│   ├── public/                # 站点静态资源（logo 等）
│   └── .vitepress/            # VitePress 配置与主题
├── code/                      # 逐章代码，每章目录独立可跑
│   ├── .env.example
│   ├── ch01/main.py           # 第 1–10 章都是单文件，方便 diff
│   └── …ch15/checkup.py
├── .github/workflows/         # 推送 main 自动构建并发布到 GitHub Pages
└── package.json               # VitePress 依赖与文档站脚本
```

本地预览文档站：

```bash
npm install
npm run docs:dev        # http://localhost:5173/mini-workbuddy/
npm run docs:build      # 产物在 docs/.vitepress/dist
```

## 🗺 后续计划

| 计划 | 内容 | 状态 |
|---|---|---|
| **配套视频教程** | 按章节录制，一章一集。重点是"跑起来"和"踩坑现场"：环境配置、第一次工具调用、ReAct 循环打断点、前端 SSE 联调，这些光看文字最费劲的部分优先上视频。 | 筹备中 |
| **接入 pi-agent** | 在现有 AgentScope 主线之外补一条适配路径：把 mini-workbuddy 的工具、状态与事件流对接 pi-agent，验证同一套产品骨架换运行时后仍然成立，并沉淀一份对照说明。 | 筹备中 |

## ✨核心贡献者
- [牧小熊-项目负责人](https://github.com/muxiaoxiong)(Datawhale成员)

## ❤️特别感谢

- 感谢 [@Sm1les](https://github.com/Sm1les) 对本项目的帮助与支持
- 感谢所有为本项目做出贡献的开发者们 ❤️

<div align="left">

<a href="https://github.com/datawhalechina/mini-workbuddy/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=datawhalechina/mini-workbuddy&v=2" />
</a>

</div>


## 🧑‍💻 参与贡献

- 发现错误或表述不清：提 Issue，附上章节号与实际看到的输出。
- 想补充内容或修正代码：提 Pull Request，一章一个提交，便于逐章审阅。
- 新增配图请遵循既有视觉规范：浅色底、1200 像素宽、源文件放 `assets/src/`，命名 `<章号>-<主题>`。

## 📄 许可

教程正文采用 [CC BY-NC-SA 4.0](http://creativecommons.org/licenses/by-nc-sa/4.0/) 许可：署名、非商业性使用、相同方式共享。
`code/` 下的示例代码可自由用于学习与商业项目，无需署名。
