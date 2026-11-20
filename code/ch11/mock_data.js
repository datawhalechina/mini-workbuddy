/* 第 11 章的演示数据：一份"假的事件流"
 *
 * 为什么要有这个文件？
 *   前端的工作对象是事件，不是模型。只要有人按同样的格式吐事件，
 *   前端就能开发、能调试、能截图——不用等 key 有效，也不用等后端写完。
 *
 * 用法：
 *   mock.html                  空白开场，可以自己随便问
 *   mock.html?scene=stream     流式输出中
 *   mock.html?scene=tool       工具执行中
 *   mock.html?scene=error      出错
 *   mock.html?scene=task       任务清单
 */

(function () {
  const H = window.WB.handle;
  const S = window.WB.state;

  // 假时钟：让耗时看起来像真的，而不是一律 0ms
  let clock = 1761180000;
  const tick = (ms) => (clock += ms / 1000);

  const emit = (type, data = {}) => {
    H(type, Object.assign({ reply_id: "r-demo", ts: clock }, data));
  };

  /* ---------------- 左侧会话列表 ---------------- */

  const SESSIONS = [
    { t: "重构门户首页", m: 12 },
    { t: "分析招投标文件", m: 8 },
    { t: "写一份权限设计说明", m: 21 },
    { t: "第 11 章前端联调", m: 6 },
  ];

  function renderSide(title) {
    document.getElementById("session-title").textContent = title;
    document.getElementById("sessions").innerHTML = SESSIONS.map(
      (s, i) => `<div class="session-item ${i === 3 ? "active" : ""}">
        <div class="t">${s.t}</div>
        <div class="m">${s.m} 条消息</div>
      </div>`,
    ).join("");
  }

  /* ---------------- 三种动作的小工具 ---------------- */

  function userSays(text) {
    S.messages.push({
      id: `u-${Date.now()}`,
      role: "user",
      blocks: [{ key: "t", kind: "text", text }],
    });
    window.WB.renderMessages();
  }

  function think(text, id = "th-1") {
    emit("THINKING_BLOCK_DELTA", { block_id: id, delta: text });
  }

  // 一次回复里的文本可以分成多个块：模型说完一句去调工具，回来接着说，
  // 这两段就是两个块。前端按 block_id 分，不能按顺序硬拼。
  function say(text, id = "tx-1") {
    emit("TEXT_BLOCK_DELTA", { block_id: id, delta: text });
  }

  function lockUI() {
    document.getElementById("btn-send").disabled = true;
    document.getElementById("btn-send").textContent = "生成中";
  }

  // 工具参数是流式到达的：这里切成几片，模拟真实的分片输入
  function toolCall(id, name, args, { ms = 240, finish = true } = {}) {
    tick(120);
    emit("TOOL_CALL_START", { tool_call_id: id, tool_call_name: name });
    const piece = Math.ceil(args.length / 4);
    for (let i = 0; i < args.length; i += piece) {
      tick(12);
      emit("TOOL_CALL_DELTA", { tool_call_id: id, delta: args.slice(i, i + piece) });
    }
    tick(ms);
    if (finish) emit("TOOL_CALL_END", { tool_call_id: id });
    return clock;
  }

  function toolResult(id, output, { ok = true } = {}) {
    emit("TOOL_RESULT_START", { tool_call_id: id, tool_call_name: "" });
    const lines = output.split("\n");
    for (const line of lines) {
      tick(3);
      emit("TOOL_RESULT_TEXT_DELTA", { tool_call_id: id, delta: line + "\n" });
    }
    tick(40);
    emit("TOOL_RESULT_END", { tool_call_id: id, state: ok ? "success" : "error" });
  }

  function usage(inTok, outTok) {
    emit("MODEL_CALL_END", { input_tokens: inTok, output_tokens: outTok });
  }

  function openTools() {
    document.querySelectorAll(".tool-card").forEach((el) => {
      el.classList.add("open");
      el.querySelector(".ic").textContent = "▾";
    });
  }

  function openThinking() {
    const el = document.querySelector(".block-thinking");
    if (!el) return;
    el.classList.add("open");
    el.querySelector(".toggle").textContent = "▾ 思考过程";
    const m = S.messages[S.messages.length - 1];
    const b = m.blocks.find((x) => x.kind === "thinking");
    if (b) el.querySelector(".body").textContent = b.text;
  }

  /* ---------------- 场景一：什么都不说 ---------------- */

  function sceneEmpty() {
    renderSide("第 11 章前端联调");
    window.WB.renderTasks([]);
  }

  /* ---------------- 场景二：正在流式输出 ---------------- */

  function sceneStream() {
    renderSide("第 11 章前端联调");
    userSays("事件流里 text_block_delta 和 thinking_block_delta 有什么区别？");
    tick(80);
    emit("REPLY_START", { role: "assistant" });

    think(
      "两者都是增量，区别在语义而不是格式：thinking 是模型的中间推理，" +
        "text 是最终回答。渲染上不能混在一起，否则用户没法折叠思考过程。",
    );
    say("格式上完全一样，都是一个 block_id 加一段 delta。差别在语义：");
    say("\n\n- thinking_block_delta 是模型的中间推理，属于过程，可以折叠；");
    say("\n- text_block_delta 是最终回答，属于结果，必须一直可见。");
    say("\n\n渲染时按 block_id 分组，同一条消息里可以有多个块……");

    usage(4820, 265);
    // 故意不发 REPLY_END：界面停在"生成中"
  }

  /* ---------------- 场景三：工具执行中 ---------------- */

  function sceneTool() {
    renderSide("第 11 章前端联调");
    userSays("assets 里的图现在一共多少张？");
    tick(60);
    emit("REPLY_START", { role: "assistant" });

    think("① Glob 找 png；② 从返回结果数条数；③ 不读图片内容，只看路径。");
    say("先扫一遍目录。");

    toolCall("c-1", "Glob", '{"pattern": "**/*.png", "path": "assets"}');
    toolResult(
      "c-1",
      [
        "assets/ch07/permission-flow.png",
        "assets/ch07/hitl-sequence.png",
        "assets/ch06/toolbase.png",
        "…（共 20 个文件）",
      ].join("\n"),
    );
    usage(6120, 118);

    say("20 张。接着看第二章的图是怎么画出来的。", "tx-2");

    // 第二个调用不结束：界面停在"执行中"
    toolCall("c-2", "Read", '{"path": "assets/src/12-chat-ui.html"}', { finish: false });
    usage(7010, 96);
  }

  /* ---------------- 场景四：出错 ---------------- */

  function sceneError() {
    renderSide("第 11 章前端联调");
    userSays("把 assets 里没用的图删掉。");
    tick(60);
    emit("REPLY_START", { role: "assistant" });

    think("删除不可逆，先列候选再确认，不能直接批量删。");
    say("先看看候选文件。");

    toolCall("c-9", "PowerShell", '{"command": "Remove-Item assets/old/*.png -Force"}', {
      ms: 180,
    });
    toolResult(
      "c-9",
      "PermissionError: 当前权限模式（default）要求删除操作先确认，\n" +
        "但本次会话没有接入确认通道（第 12 章补上）。",
      { ok: false },
    );
    usage(5340, 143);

    emit("error", {
      message:
        "PermissionError: 工具 PowerShell 的调用被拒绝。这次的拒绝来自权限引擎，不是工具自己报错。",
    });
    window.WB.state.stats.calls = 1;
    window.WB.state.stats.tools = 1;
    window.WB.renderStats();
  }

  /* ---------------- 场景五：任务清单 ---------------- */

  function sceneTask() {
    renderSide("第 11 章前端联调");

    window.WB.renderTasks([
      { subject: "写 style.css 三栏骨架", state: "completed" },
      { subject: "整理事件 reducer", state: "completed" },
      { subject: "接上 SSE 客户端", state: "in_progress" },
      { subject: "截图五张并插进正文", state: "pending" },
    ]);

    userSays("把第 11 章的前端补完。");
    tick(60);
    emit("REPLY_START", { role: "assistant" });

    think("拆成四件能独立验收的小事，做完一件划掉一件。");
    say("骨架和 reducer 已经好了，现在接流式客户端。");
    say("\n\n剩下两件：先把 fetch + ReadableStream 的读帧循环写完，再按 ");
    say("`\\n\\n` 分帧；分帧写对了，五张截图才有稳定的状态可拍。");

    emit("HINT_BLOCK", { hint: "<current-time>2026-10-22 14:08</current-time>" });
    usage(5980, 212);
  }

  const SCENES = {
    empty: sceneEmpty,
    stream: sceneStream,
    tool: sceneTool,
    error: sceneError,
    task: sceneTask,
  };

  /* ---------------- 启动 ---------------- */

  window.WB_MOCK_BOOT = function () {
    S.sessionId = "mock-0001";
    window.WB.setConn("ok", "演示模式");
    const scene = SCENES[window.__MOCK_SCENE] || sceneEmpty;
    scene();

    // 演示数据是一次性同步吐完的，"本轮耗时"会停在 0.0s。
    // 把它拨回几秒前，截图里才像真的跑过一轮。
    if (S.stats.startAt) {
      S.stats.startAt = performance.now() - 12_400;
      window.WB.renderStats();
    }
    // 流式中和工具执行中都不该让用户再发一条
    if (["stream", "tool"].includes(window.__MOCK_SCENE)) lockUI();

    // 截图要看内部结构，所以默认展开；正常聊天不用全展开
    if (["tool", "stream", "task"].includes(window.__MOCK_SCENE)) openThinking();
    if (window.__MOCK_SCENE === "tool") {
      document.querySelectorAll(".tool-card")[0]?.classList.add("open");
      const ic = document.querySelectorAll(".tool-card")[0]?.querySelector(".ic");
      if (ic) ic.textContent = "▾";
    }
  };

  /* ---------------- 交互：随便问一句也有回应 ---------------- */

  window.__MOCK_REPLAY = function (text) {
    const steps = [];

    steps.push(() => {
      tick(60);
      emit("REPLY_START", { reply_id: "r-live", role: "assistant" });
    });
    steps.push(() => think("（演示模式）没有真的模型，这里回放一段写好的事件流。"));
    const answer =
      `你问的是「${text}」。演示模式没有接模型，所以这是一段写死的事件流。` +
      "真实运行时，这里会是模型逐字吐出来的回答。";
    for (const ch of answer.match(/.{1,6}/gs)) {
      steps.push(() => say(ch));
    }
    steps.push(() => usage(3200, 88));
    steps.push(() => emit("REPLY_END", { reply_id: "r-live", finished_reason: "completed" }));

    (function next(i) {
      if (i >= steps.length) return;
      steps[i]();
      setTimeout(() => next(i + 1), 24);
    })(0);
  };

  document.getElementById("btn-new").onclick = () => {
    S.messages = [];
    S.stats = { calls: 0, tools: 0, inTok: 0, outTok: 0, startAt: 0 };
    window.WB.renderMessages();
    window.WB.renderStats();
    window.WB.renderTasks([]);
    document.getElementById("log").textContent = "";
  };
})();
