// 在 Node 中运行真实 app.js；只替换浏览器 DOM 和 HTTP 传输，不需要模型或浏览器依赖。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const flush = () => new Promise(setImmediate);

function loadApp(chapter, { delayInitialSession = false } = {}) {
  const elements = [];
  class Element {
    constructor() {
      this.children = [];
      this.style = {};
      this.value = this.textContent = this.innerHTML = "";
      this.disabled = false;
      this.className = "";
      this.scrollHeight = this.clientHeight = this.scrollTop = 0;
      this.parts = {};
      elements.push(this);
    }
    addEventListener() {}
    focus() {}
    appendChild(el) { this.children.push(el); el.parent = this; return el; }
    querySelector(selector) { return this.parts[selector] ||= new Element(); }
    querySelectorAll(selector) {
      return this.children.filter((el) => el.className.split(" ").includes(selector.slice(1)));
    }
    remove() {
      this.removed = true;
      if (this.parent) this.parent.children = this.parent.children.filter((el) => el !== this);
    }
  }
  const document = {
    body: new Element(),
    createElement: () => new Element(),
    getElementById: (id) => elements.find((el) => el.id === id && !el.removed) || null,
    querySelectorAll: () => [],
  };
  for (const [, id] of fs.readFileSync(path.join(chapter, "index.html"), "utf8").matchAll(/id="([^"]+)"/g)) {
    const el = new Element(); el.id = id;
  }
  const streams = [], requests = [];
  const harness = { document, streams, requests, failNextSession: false, usageResponses: [] };
  let sessions = 0;
  const fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    if (url.startsWith("/api/stream/")) {
      let waiter;
      const stream = {
        url, signal: options.signal,
        // 故意延迟响应 abort，复现旧流晚于新流结束的顺序。
        emit(type, data, id = 1) {
          waiter({ value: new TextEncoder().encode(`id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`), done: false });
        },
        finish() { waiter({ done: true }); },
      };
      streams.push(stream);
      return { ok: true, body: { getReader: () => ({ read: () => new Promise((resolve) => { waiter = resolve; }) }) } };
    }
    if (url === "/api/sessions" && options.method === "POST") {
      if (harness.failNextSession) {
        harness.failNextSession = false;
        return { ok: false, status: 503, json: async () => ({ detail: "unavailable" }) };
      }
      const sessionId = `session-${++sessions}`;
      const response = { ok: true, json: async () => ({ session_id: sessionId }) };
      if (delayInitialSession && sessions === 1) {
        return new Promise((resolve) => {
          harness.finishInitialSession = () => resolve(response);
        });
      }
      if (harness.delayNextSession) {
        harness.delayNextSession = false;
        return new Promise((resolve) => {
          harness.finishDelayedSession = () => resolve(response);
        });
      }
      return response;
    }
    if (url.startsWith("/api/usage") && harness.holdUsage) {
      return new Promise((resolve) => {
        harness.usageResponses.push((data) => resolve({ ok: true, json: async () => data }));
      });
    }
    return { ok: true, json: async () => ({ items: [], summary: {} }) };
  };
  const window = { addEventListener() {} };
  const context = vm.createContext({
    window, document, fetch, AbortController, TextDecoder, URLSearchParams,
    location: { search: "" }, performance, Date, setTimeout,
    requestAnimationFrame: (fn) => fn(), console,
  });
  vm.runInContext(fs.readFileSync(path.join(chapter, "app.js"), "utf8"), context);
  harness.state = window.WB.state;
  harness.wb = window.WB;
  harness.clickNew = async () => {
    const button = document.getElementById("btn-new");
    assert.equal(typeof button.onclick, "function", "新建按钮应绑定会话创建动作");
    await button.onclick();
    await flush();
  };
  return harness;
}

function sessionTests(chapter) {
  test(`${path.basename(chapter)} 新建清空当前界面并连接新会话`, async () => {
    const h = loadApp(chapter);
    await flush();
    h.wb.handle("REPLY_START", { reply_id: "old-reply" });
    h.wb.handle("MODEL_CALL_END", { input_tokens: 9, output_tokens: 4 });
    h.wb.renderTasks([{ subject: "old task", state: "in_progress" }]);
    h.document.getElementById("input").value = "old draft";
    h.document.getElementById("btn-send").disabled = true;
    if (h.document.getElementById("btn-stop")) {
      h.wb.handle("REQUIRE_USER_CONFIRM", { tool_calls: [{ id: "old-call", name: "Write", input: "{}" }] });
    }
    await h.clickNew();
    assert.equal(h.state.sessionId, "session-2");
    assert.equal(h.streams.length, 2);
    assert.equal(h.streams[0].signal.aborted, true);
    assert.equal(h.state.messages.length, 0);
    assert.equal(h.document.getElementById("messages").querySelectorAll(".msg").length, 0);
    assert.equal(h.state.tasks.length, 0);
    assert.equal(h.state.stats.calls, 0);
    assert.equal(h.state.streaming, false);
    assert.equal(h.document.getElementById("input").value, "");
    assert.equal(h.document.getElementById("btn-send").disabled, false);
    assert.equal(h.document.getElementById("log").textContent, "");
    assert.equal(h.document.getElementById("confirm"), null);
    assert.equal(h.requests.some((r) => r.url === "/api/interrupt"), false);
  });

  test(`${path.basename(chapter)} 旧流的帧和收尾不影响新连接`, async () => {
    const h = loadApp(chapter);
    await flush();
    await h.clickNew();
    const active = h.state.abort;
    h.streams[0].emit("MODEL_CALL_END", { input_tokens: 100 }, 99);
    await flush();
    h.streams[0].finish();
    await flush();
    assert.equal(h.state.stats.calls, 0);
    assert.equal(h.state.lastEventId, 0);
    assert.equal(h.state.abort, active);
    assert.equal(h.state.connecting, true);
    assert.match(h.document.getElementById("conn").innerHTML, /已连接/);
    h.streams[1].emit("MODEL_CALL_END", { input_tokens: 3 }, 1);
    await flush();
    assert.equal(h.state.stats.calls, 1);
    assert.equal(h.state.stats.inTok, 3);
  });

  test(`${path.basename(chapter)} 创建失败保留旧会话`, async () => {
    const h = loadApp(chapter);
    await flush();
    h.failNextSession = true;
    await h.clickNew();
    assert.equal(h.state.sessionId, "session-1");
    assert.equal(h.streams.length, 1);
    assert.equal(h.streams[0].signal.aborted, false);
    assert.match(h.document.getElementById("status").textContent, /503/);
    assert.equal(h.document.getElementById("btn-new").disabled, false);
  });

  test(`${path.basename(chapter)} 启动创建请求迟到不覆盖后来新建的会话`, async () => {
    const h = loadApp(chapter, { delayInitialSession: true });
    await flush();
    await h.clickNew();
    h.wb.handle("REPLY_START", { reply_id: "new-reply" });
    h.wb.handle("TEXT_BLOCK_DELTA", { reply_id: "new-reply", block_id: "new-text", delta: "保留这段回复" });
    h.finishInitialSession();
    await flush();
    assert.equal(h.state.sessionId, "session-2");
    assert.equal(h.streams.length, 1);
    assert.equal(h.streams[0].signal.aborted, false);
    assert.equal(h.state.messages.length, 1);
    assert.equal(h.state.messages[0].blocks[0].text, "保留这段回复");
    assert.equal(h.state.streaming, true);
  });

  test(`${path.basename(chapter)} 过期的发送请求不把旧文本发到新会话`, async () => {
    const h = loadApp(chapter, { delayInitialSession: true });
    await flush();
    h.delayNextSession = true;
    h.document.getElementById("input").value = "旧会话的问题";
    const sending = h.document.getElementById("btn-send").onclick();
    await h.clickNew();
    h.finishDelayedSession();
    await sending;
    await flush();
    assert.equal(h.state.sessionId, "session-3");
    assert.equal(h.state.messages.length, 0);
    assert.equal(h.requests.some((r) => r.url === "/api/chat"), false);
    assert.equal(h.streams.length, 1);
  });
}

module.exports = { sessionTests, loadApp, flush };
