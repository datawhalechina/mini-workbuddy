const assert = require("node:assert/strict");
const { test } = require("node:test");
const { sessionTests, loadApp, flush } = require("../ch12/session_test_helpers.cjs");

sessionTests(__dirname);

test("ch14 旧会话的账单响应不会覆盖新会话", async () => {
  const h = loadApp(__dirname);
  await flush();
  h.holdUsage = true;
  h.wb.handle("done", {});
  await h.clickNew();
  h.usageResponses[0]({ summary: { cost: 123, cached_tokens: 456 } });
  h.usageResponses[1]({ items: [] });
  await flush();
  assert.equal(h.document.getElementById("s-cost").textContent, "¥0.0000");
  assert.equal(h.document.getElementById("s-cache").textContent, "0");
});
