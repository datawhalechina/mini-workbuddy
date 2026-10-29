/* 第 12 章前端：把事件流拼成界面
 *
 * 三件事：
 *   1. 一个 40 行的 SSE 客户端（fetch + ReadableStream，不用 EventSource）
 *   2. 一个事件 reducer：按 reply_id / block_id 把增量归位
 *   3. 一个局部渲染器：只更新变化的那一小块，不整页重绘
 */

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  sessionRequest: 0,
  messages: [],       // { id, role, blocks: [], els: {}, timelineEl }
  tasks: [],
  stats: { calls: 0, tools: 0, inTok: 0, outTok: 0, startAt: 0 },
  lastEventId: 0,
  streaming: false,
  connecting: false,
  stick: true,        // 是否吸底跟随
  abort: null,
};

const MOCK = Boolean(window.WB_MOCK);

/* ------------------------------------------------------------------ */
/* 一、SSE 客户端                                                      */
/* ------------------------------------------------------------------ */

// EventSource 有个致命问题：连接一断它就自动重连。
// 我们用 done 主动收尾，EventSource 会立刻重连，形成死循环。
// 所以这里手写：解析 \n\n 分帧，done 之后主动停止，网络错误才重连。
async function readStream(url, { onFrame, onOpen, signal }) {
  const res = await fetch(url, { signal, headers: { Accept: "text/event-stream" } });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  if (onOpen) onOpen();

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const frame = parseFrame(raw);
      if (frame) onFrame(frame);
    }
  }
}

function parseFrame(raw) {
  let id = null, event = "message", data = "";
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;      // 注释帧：心跳
    const i = line.indexOf(":");
    const key = line.slice(0, i);
    const val = line.slice(i + 1).trimStart();
    if (key === "id") id = Number(val);
    else if (key === "event") event = val;
    else if (key === "data") data += val;
  }
  if (!data) return null;
  try {
    return { id, event, data: JSON.parse(data) };
  } catch {
    return null;
  }
}

async function connect() {
  if (MOCK || !state.sessionId) return;
  // 收到 done 时后端会关掉这条流，所以每一轮发送之后都要重新连。
  // 用 connecting 去重，避免连续点两次发送时开两条流抢同一批帧。
  if (state.connecting) return;
  const controller = new AbortController();
  const sessionId = state.sessionId;
  state.abort = controller;
  state.connecting = true;
  setConn("busy", "连接中");

  try {
    while (!controller.signal.aborted) {
      try {
        await readStream(
          `/api/stream/${sessionId}?last_event_id=${state.lastEventId}`,
          {
            signal: controller.signal,
            onOpen: () => {
              if (state.abort === controller) setConn("ok", "已连接");
            },
            onFrame: (frame) => {
              if (state.abort !== controller || controller.signal.aborted) return;
              if (frame.id) state.lastEventId = frame.id;
              // 注意：不要在这里因为 done 就中断读取。
              // 回放历史帧时，缓冲区里会有多个 done（每轮一个），
              // 碰到第一个就停，后面的几轮永远渲染不出来。
              // fetch 不会自动重连，让服务端关掉连接即可。
              handle(frame.event, frame.data);
            },
          },
        );
        break;                                   // 正常结束
      } catch (err) {
        if (controller.signal.aborted || err.name === "AbortError") break;
        setConn("off", "连接断开，3 秒后重连");
        await new Promise((r) => setTimeout(r, 3000));
      }
    }
  } finally {
    // 切换会话后，旧连接的收尾不能覆盖新连接的状态。
    if (state.abort === controller) {
      state.connecting = false;
      state.abort = null;
      setConn("off", "本轮结束");
    }
  }
}

/* ------------------------------------------------------------------ */
/* 二、事件 reducer                                                    */
/* ------------------------------------------------------------------ */

function currentMessage() {
  return state.messages[state.messages.length - 1];
}

function handle(type, d) {
  addLog(type, d);

  switch (type) {
    case "REPLY_START": {
      state.streaming = true;
      state.stats.startAt = performance.now();
      state.messages.push({
        id: d.reply_id, role: "assistant", blocks: [], els: {}, status: "running",
      });
      renderMessages();
      break;
    }

    case "TEXT_BLOCK_DELTA": {
      const msg = findByReply(d.reply_id) || currentMessage();
      if (!msg) break;
      const block = ensureBlock(msg, d.block_id, "text");
      block.text += d.delta || "";
      appendText(msg, block);
      break;
    }

    case "THINKING_BLOCK_DELTA": {
      const msg = findByReply(d.reply_id) || currentMessage();
      if (!msg) break;
      const block = ensureBlock(msg, d.block_id, "thinking");
      block.text += d.delta || "";
      updateThinking(msg, block);
      break;
    }

    case "TOOL_CALL_START": {
      const msg = findByReply(d.reply_id) || currentMessage();
      if (!msg) break;
      const block = ensureBlock(msg, d.tool_call_id, "tool");
      block.name = d.tool_call_name;
      block.status = "running";
      block.input = "";
      block.output = "";
      block.startedAt = d.ts ? d.ts * 1000 : performance.now();
      renderTool(msg, block);
      state.stats.tools += 1;
      renderStats();
      break;
    }

    // 参数不在 START 里，而是在一串 DELTA 里逐片到达。
    // 拼完之前不要 JSON.parse——半截 JSON 一定报错。
    case "TOOL_CALL_DELTA": {
      const msg = findByReply(d.reply_id) || currentMessage();
      if (!msg) break;
      const block = ensureBlock(msg, d.tool_call_id, "tool");
      block.input = (block.input || "") + (d.delta || "");
      renderTool(msg, block);
      break;
    }

    case "TOOL_RESULT_TEXT_DELTA": {
      const msg = findByReply(d.reply_id) || currentMessage();
      if (!msg) break;
      const block = ensureBlock(msg, d.tool_call_id, "tool");
      block.output = (block.output || "") + (d.delta || "");
      renderTool(msg, block);
      break;
    }

    case "TOOL_RESULT_END": {
      const msg = findByReply(d.reply_id) || currentMessage();
      if (!msg) break;
      const block =
        msg.blocks.find((b) => b.kind === "tool" && b.key === d.tool_call_id) ||
        msg.blocks.find((b) => b.kind === "tool" && b.status === "running");
      if (block) {
        block.status = d.state === "error" ? "error" : "success";
        // 用服务端时间戳算耗时。前端本地时钟可能和服务器差几分钟，
        // 用 performance.now() 相减会在网络慢的时候把等待时间也算进去。
        block.elapsed = (d.ts ? d.ts * 1000 : performance.now()) - block.startedAt;
        renderTool(msg, block);
        renderTimeline(msg);
      }
      break;
    }

    case "MODEL_CALL_END": {
      state.stats.calls += 1;
      state.stats.inTok += d.input_tokens || 0;
      state.stats.outTok += d.output_tokens || 0;
      renderStats();
      break;
    }

    case "HINT_BLOCK":
      // 注入块：模型看到的"现在是几点""还剩多少上下文"就藏在这里
      addLog("HINT_BLOCK", { delta: d.hint });
      break;

    case "REQUIRE_USER_CONFIRM": {
      const msg = currentMessage();
      if (msg) msg.status = "waiting";
      showConfirm(d.tool_calls || []);
      break;
    }

    case "confirm_required":
      setStatus("等你确认");
      break;

    case "error": {
      const msg = currentMessage();
      if (msg) {
        msg.status = "error";
        msg.blocks.push({ kind: "notice", text: d.message, level: "error" });
        renderMessages();
      }
      setStatus("出错了");
      break;
    }

    case "REPLY_END": {
      state.streaming = false;
      renderStats();
      setStatus("");
      unlockInput();
      break;
    }

    case "done": {
      state.streaming = false;
      unlockInput();
      setStatus("");
      renderStats();
      break;
    }
  }
}

function findByReply(replyId) {
  for (let i = state.messages.length - 1; i >= 0; i--) {
    if (state.messages[i].id === replyId) return state.messages[i];
  }
  return null;
}

function ensureBlock(msg, blockId, kind) {
  const key = blockId || `${kind}-${msg.blocks.length}`;
  let block = msg.blocks.find((b) => b.key === key);
  if (!block) {
    block = { key, kind, text: "" };
    msg.blocks.push(block);
  }
  return block;
}

/* ------------------------------------------------------------------ */
/* 三、渲染（局部更新）                                                */
/* ------------------------------------------------------------------ */

const messagesEl = () => $("messages");

function renderMessages() {
  const box = messagesEl();
  if (state.messages.length === 0) {
    if ($("empty")) $("empty").style.display = "";
    return;
  }
  if ($("empty")) $("empty").style.display = "none";

  for (const msg of state.messages) {
    if (!msg.el) {
      const el = document.createElement("div");
      el.className = `msg ${msg.role}`;
      el.innerHTML = `<div class="wrap">
        <div class="who">${msg.role === "user" ? "你" : "Buddy"}</div>
        <div class="bubble"></div>
        <div class="timeline"></div>
      </div>`;
      msg.el = el;
      msg.bubbleEl = el.querySelector(".bubble");
      msg.timelineEl = el.querySelector(".timeline");
      box.appendChild(el);
    }
    // 把还没进 DOM 的块补进去
    for (const b of msg.blocks) {
      if (!b.el) buildBlockEl(msg, b);
    }
  }
  scrollToBottom();
}

function buildBlockEl(msg, block) {
  if (block.kind === "text") {
    const el = document.createElement("div");
    el.className = "block-text";
    el.textContent = block.text;
    block.el = el;
    msg.bubbleEl.appendChild(el);
  } else if (block.kind === "thinking") {
    const el = document.createElement("div");
    el.className = "block-thinking";
    el.innerHTML = `<div class="toggle">▸ 思考过程</div><div class="body"></div>`;
    el.querySelector(".toggle").onclick = () => {
      el.classList.toggle("open");
      el.querySelector(".toggle").textContent =
        (el.classList.contains("open") ? "▾" : "▸") + " 思考过程";
    };
    block.el = el;
    block.bodyEl = el.querySelector(".body");
    msg.bubbleEl.appendChild(el);
  } else if (block.kind === "tool") {
    renderTool(msg, block);
  } else if (block.kind === "notice") {
    const el = document.createElement("div");
    el.className = `notice ${block.level}`;
    el.textContent = block.text;
    block.el = el;
    msg.bubbleEl.appendChild(el);
  }
}

function appendText(msg, block) {
  if (!block.el) {
    buildBlockEl(msg, block);
    renderTimeline(msg);
  }
  block.el.textContent = block.text;
  scrollToBottom();
}

function updateThinking(msg, block) {
  if (!block.el) buildBlockEl(msg, block);
  block.bodyEl.textContent = block.text;
}

function renderTool(msg, block) {
  if (!block.el) {
    const el = document.createElement("div");
    el.className = "tool-card";
    el.innerHTML = `<div class="hd">
        <span class="ic">▸</span>
        <span class="name"></span>
        <span class="args"></span>
        <span class="st"></span>
      </div><div class="bd"></div>`;
    el.querySelector(".hd").onclick = () => {
      el.classList.toggle("open");
      el.querySelector(".ic").textContent =
        el.classList.contains("open") ? "▾" : "▸";
    };
    block.el = el;
    msg.bubbleEl.appendChild(el);
  }

  const label = { running: "执行中", success: "完成", error: "失败" }[block.status];
  block.el.querySelector(".name").textContent = block.name || "tool";
  block.el.querySelector(".args").textContent = formatArgs(block);
  const st = block.el.querySelector(".st");
  st.className = `st ${block.status}`;
  st.textContent = label;

  if (block.output) {
    block.el.querySelector(".bd").textContent = block.output;
  }
  scrollToBottom();
}

function formatArgs(block) {
  if (!block.input) return "";
  try {
    const obj = JSON.parse(block.input);
    return Object.entries(obj)
      .map(([k, v]) => `${k}=${String(v).slice(0, 28)}`)
      .join("  ");
  } catch {
    return String(block.input).slice(0, 40);
  }
}

// 工具时间线：一条消息里所有调用的横向概览
function renderTimeline(msg) {
  const tools = msg.blocks.filter((b) => b.kind === "tool");
  if (!msg.timelineEl) return;
  if (tools.length === 0) {
    msg.timelineEl.innerHTML = "";
    return;
  }
  msg.timelineEl.innerHTML = tools
    .map((b) => {
      const cls = b.status === "running" ? "run" : b.status === "error" ? "bad" : "ok";
      const ic = b.status === "running" ? "◌" : b.status === "error" ? "✕" : "✓";
      const ms = b.elapsed ? `<span class="ms">${Math.round(b.elapsed)}ms</span>` : "";
      return `<span class="tl-item ${cls}"><span class="ic">${ic}</span>${b.name || "tool"}${ms}</span>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/* 四、滚动：用户往回看时不要抢他的位置                                */
/* ------------------------------------------------------------------ */

function scrollToBottom() {
  if (!state.stick) return;
  const box = messagesEl();
  box.scrollTop = box.scrollHeight;
}

messagesEl().addEventListener("scroll", () => {
  const box = messagesEl();
  const gap = box.scrollHeight - box.scrollTop - box.clientHeight;
  state.stick = gap < 40;
});

/* ------------------------------------------------------------------ */
/* 五、输入与发送                                                      */
/* ------------------------------------------------------------------ */

const input = $("input");

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 140) + "px";
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

$("btn-send").onclick = send;

document.querySelectorAll(".example").forEach((el) => {
  el.onclick = () => {
    input.value = el.dataset.q;
    send();
  };
});

async function send() {
  const text = input.value.trim();
  if (!text) return;

  if (!state.sessionId) {
    const created = await newSession(MOCK ? text.slice(0, 20) : undefined);
    if (!created) return;   // 等待创建期间切换了会话，旧问题不应发到新会话。
  }

  if (state.streaming) {
    setStatus("上一条还没结束");
    return;
  }

  state.messages.push({ id: `u-${Date.now()}`, role: "user", blocks: [{ key: "t", kind: "text", text }], els: {} });
  input.value = "";
  input.style.height = "auto";
  lockInput();
  state.stick = true;
  renderMessages();

  if (MOCK) {
    replayMock(text);
    return;
  }

  await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, text }),
  });
  connect();      // 后端在 done 时会关流，每一轮都要重新接上
}

function lockInput() {
  $("btn-send").disabled = true;
  $("btn-send").textContent = "生成中";
}

function unlockInput() {
  $("btn-send").disabled = false;
  $("btn-send").textContent = "发送";
  input.focus();
}

/* ------------------------------------------------------------------ */
/* 六、会话与右栏                                                      */
/* ------------------------------------------------------------------ */

$("btn-new").onclick = async () => {
  $("btn-new").disabled = true;
  try {
    await newSession();
  } catch (err) {
    setStatus(`新建会话失败：${err.message}`);
  } finally {
    $("btn-new").disabled = false;
  }
};

async function newSession(title) {
  const request = ++state.sessionRequest;
  let sessionId = "mock-0001";
  if (!MOCK) {
    const res = await fetch("/api/sessions", { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    sessionId = data.session_id;
  }
  // 启动请求可能晚于用户点击“新建”的请求返回，只接纳最新一次。
  if (request !== state.sessionRequest) return false;
  // 新会话创建成功后再离开旧会话；只断开订阅，后台任务继续运行。
  state.abort?.abort();
  state.abort = null;
  state.connecting = false;
  state.sessionId = sessionId;
  state.lastEventId = 0;
  state.messages = [];
  state.streaming = false;
  state.stick = true;
  state.stats = { calls: 0, tools: 0, inTok: 0, outTok: 0, startAt: 0 };
  messagesEl().querySelectorAll(".msg").forEach((el) => el.remove());
  renderMessages();
  renderTasks([]);
  renderStats();
  $("log").textContent = "";
  input.value = "";
  input.style.height = "auto";
  setStatus("");
  unlockInput();
  if (MOCK) {
    $("session-title").textContent = title || "演示会话";
    renderSessions([{ id: sessionId, title: title || "演示会话", messages: 0 }]);
    setConn("ok", "演示模式");
    return true;
  }
  $("session-title").textContent = "新会话";
  await refreshSessions();
  if (request !== state.sessionRequest) return false;
  connect();
  return true;
}

async function refreshSessions() {
  if (MOCK) return;
  const res = await fetch("/api/sessions");
  const data = await res.json();
  renderSessions(
    data.items.map((s) => ({
      id: s.session_id,
      title: s.session_id,
      messages: s.messages,
      running: s.running,
    })),
  );
}

function renderSessions(items) {
  $("sessions").innerHTML = items
    .map(
      (s) => `<div class="session-item ${s.id === state.sessionId ? "active" : ""}">
        <div class="t">${s.title}</div>
        <div class="m">${s.messages} 条消息</div>
      </div>`,
    )
    .join("");
}

function renderTasks(tasks) {
  state.tasks = tasks;
  $("task-count").textContent = tasks.filter((t) => t.state !== "completed").length;
  if (tasks.length === 0) {
    $("tasks").innerHTML = `<div style="font-size:12.5px;color:#9aa4b2;">暂无任务</div>`;
    return;
  }
  $("tasks").innerHTML = tasks
    .map((t) => {
      const cls = t.state === "completed" ? "done" : t.state === "in_progress" ? "doing" : "";
      return `<div class="task ${cls}"><span class="box"></span>
        <span class="sub">${t.subject}</span></div>`;
    })
    .join("");
}

function renderStats() {
  $("s-calls").textContent = state.stats.calls;
  $("s-tools").textContent = state.stats.tools;
  $("s-in").textContent = state.stats.inTok.toLocaleString();
  $("s-out").textContent = state.stats.outTok.toLocaleString();
  const ms = state.stats.startAt ? performance.now() - state.stats.startAt : 0;
  $("s-time").textContent = ms ? `${(ms / 1000).toFixed(1)}s` : "—";
}

function setConn(kind, text) {
  $("conn").innerHTML = `<span class="dot ${kind === "ok" ? "" : kind === "busy" ? "busy" : "off"}"></span> ${text}`;
}

function setStatus(text) {
  $("status").textContent = text;
}

// 事件日志：只记"发生了什么"，不把整包 JSON 倒上去。
// 排查时需要原文，就在浏览器控制台里看网络面板。
function addLog(type, d) {
  const el = $("log");
  let extra = "";
  if (d.tool_call_name) extra = ` ${d.tool_call_name}`;
  else if (d.delta) extra = ` +${String(d.delta).length} 字`;
  else if (d.input_tokens) extra = ` ${d.input_tokens}/${d.output_tokens} tok`;
  const now = new Date();
  const hms = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
  el.textContent = `${hms}  ${type}${extra}\n` + el.textContent;
}

function showConfirm(toolCalls) {
  const msg = currentMessage();
  if (!msg) return;
  const names = toolCalls.map((t) => t.name).join("、");
  msg.blocks.push({
    kind: "notice",
    level: "info",
    text: `需要确认：${names}（第 13 章接上按钮）`,
  });
  renderMessages();
}

/* ------------------------------------------------------------------ */
/* 七、演示数据（mock.html 用）                                        */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* 七、演示数据（mock.html 用）                                        */
/*                                                                     */
/* 没有可用的 key 时，靠一份"假的事件流"把界面跑起来。这不是给你自己  */
/* 看的玩具：前后端对接时，先拿假数据把前端做完，接口一好就能直接切。  */
/* ------------------------------------------------------------------ */

function replayMock(text) {
  if (window.__MOCK_REPLAY) window.__MOCK_REPLAY(text);
}

// 场景入口：?scene=empty|stream|tool|error|task
window.__MOCK_SCENE = new URLSearchParams(location.search).get("scene") || "empty";

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */

if (MOCK) {
  setConn("ok", "演示模式");
  // 等 mock_data.js 也加载完再开场
  window.addEventListener("load", () => {
    if (window.WB_MOCK_BOOT) window.WB_MOCK_BOOT();
  });
} else {
  newSession();
}

window.WB = { state, handle, renderMessages, renderTasks, renderStats, renderTimeline, setConn, replayMock };
