"use strict";
let chatId = null,
  lastTurn = null,
  retry = null,
  chatVersion = 0,
  sending = false;
const chatStorage = () => me && "folio.chat." + me.subject + "." + me.role;
function busyChat(value) {
  sending = value;
  for (const name of ["submitq", "question", "new-chat"])
    $(name).disabled = value;
  for (const b of document.querySelectorAll(
    "#chat-history button, [data-prompt]",
  ))
    b.disabled = value;
}
function resetChat() {
  if (me) sessionStorage.removeItem(chatStorage());
  chatVersion++;
  chatId = lastTurn = retry = null;
  $("result").replaceChildren();
  markCurrentChat();
  $("question").value = $("chat-progress").textContent = "";
  busyChat(false);
}
function renderTurn(question, r) {
  const turn = el("article", undefined, "chat-turn"),
    user = el("div", undefined, "user-message"),
    reply = el("div", undefined, "agent-message");
  turn.dataset.tool = r.tool;
  turn.dataset.contextUsed = String(r.used_context);
  user.append(el("small", "คุณ", "meta"), el("p", question));
  const label =
    r.status === "fallback"
      ? "ส่งต่อให้เจ้าหน้าที่แล้ว"
      : r.status === "refused"
        ? "แจ้งจากผู้ช่วย"
        : r.tool === "case_status"
          ? "สถานะจากระบบงาน"
          : "คำตอบจากเอกสาร";
  reply.append(el("strong", label), el("p", r.answer, "answer"));
  if (r.used_context)
    reply.append(
      el(
        "small",
        r.tool === "case_status"
          ? "ใช้เลขเคสจากคำตอบก่อนหน้า"
          : "ใช้หัวข้อจากคำตอบก่อนหน้า",
        "meta",
      ),
    );
  if (r.read_at && r.status === "answered")
    reply.append(el("small", "ตรวจสถานะเมื่อ " + date(r.read_at), "meta"));
  for (const item of r.sources) reply.append(source(item));
  const attribution = el("p", r.attribution, "meta");
  if (r.policy_url === "/static/response-policy.txt") {
    const link = el("a", r.attribution);
    link.href = r.policy_url;
    link.target = "_blank";
    link.rel = "noopener";
    attribution.replaceChildren(link);
  }
  reply.append(attribution);
  if (r.ticket_id) reply.append(button("ติดตามรายการ", () => go("queue")));
  const info = el("details", undefined, "trace");
  info.append(
    el("summary", "รายละเอียดการตอบ"),
    el("small", "Trace ID · " + r.trace_id, "meta"),
  );
  reply.append(info);
  turn.append(user, reply);
  return turn;
}
function markCurrentChat() {
  for (const b of $("chat-history").querySelectorAll("button")) {
    if (b.dataset.id === chatId) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  }
}
function historyButton(row) {
  const b = button(
    "",
    () => {
      if (compactMenu.matches) setMenu(false, $("main"));
      openChat(row.id, epoch);
    },
    "history-entry",
  );
  b.dataset.id = row.id;
  b.title = row.title;
  b.append(icon("message-circle"), el("span", row.title));
  return b;
}
async function openChat(id, seq) {
  const version = ++chatVersion;
  busyChat(true);
  $("chat-progress").textContent = "กำลังเปิดบทสนทนา…";
  try {
    const rows = await api("/api/conversations/" + id);
    if (version !== chatVersion || seq !== epoch) return;
    chatId = id;
    lastTurn = rows.at(-1)?.id || null;
    retry = null;
    sessionStorage.setItem(chatStorage(), id);
    $("result").replaceChildren(
      ...rows.map((r) => renderTurn(r.question, r.result)),
    );
    markCurrentChat();
    $("result").scrollTop = $("result").scrollHeight;
    $("chat-progress").textContent = "";
  } catch (err) {
    if (version === chatVersion && seq === epoch) {
      if (err.status === 404) resetChat();
      $("chat-progress").textContent = "";
      message(err.message);
    }
  } finally {
    if (version === chatVersion) busyChat(false);
  }
}
async function refreshChats(seq) {
  if (!me || sending) return;
  const version = chatVersion,
    rows = await api("/api/conversations");
  if (seq !== epoch || version !== chatVersion || sending) return;
  const selected = chatId || sessionStorage.getItem(chatStorage());
  $("chat-history").replaceChildren(...rows.map(historyButton));
  if (!rows.length)
    $("chat-history").append(el("p", "ยังไม่มีบทสนทนา", "meta"));
  if (selected && rows.some((r) => r.id === selected)) {
    await openChat(selected, seq);
  } else if (selected) resetChat();
}
$("new-chat").onclick = () => {
  if (compactMenu.matches) setMenu(false);
  resetChat();
  $("question").focus();
};
async function sendChat() {
  const question = $("question").value.trim();
  if (sending || question.length < 2) return;
  const subject = me.subject,
    version = ++chatVersion;
  if (!retry || retry.question !== question)
    retry = {
      chat: chatId || crypto.randomUUID(),
      turn_id: crypto.randomUUID(),
      parent_id: lastTurn,
      question,
    };
  const { chat, ...payload } = retry;
  busyChat(true);
  message("");
  $("chat-progress").textContent = "กำลังตรวจสอบข้อมูล…";
  try {
    const r = await api("/api/conversations/" + chat + "/messages", payload);
    if (version !== chatVersion || me?.subject !== subject) return;
    chatId = chat;
    lastTurn = payload.turn_id;
    retry = null;
    sessionStorage.setItem(chatStorage(), chat);
    const historyList = $("chat-history");
    const existing = Array.from(historyList.querySelectorAll("button")).find(
      (b) => b.dataset.id === chat,
    );
    historyList.querySelector("p")?.remove();
    historyList.prepend(
      existing || historyButton({ id: chat, title: question.slice(0, 90) }),
    );
    if (historyList.children.length > 50) historyList.lastElementChild.remove();
    markCurrentChat();
    const node = renderTurn(question, r);
    $("result").append(node);
    $("question").value = "";
    $("chat-progress").textContent = "";
    if (view === "ask") {
      $("result").scrollTop = $("result").scrollHeight;
    }
  } catch (err) {
    if (version === chatVersion && me?.subject === subject) {
      $("chat-progress").textContent =
        err.status === 409
          ? "บทสนทนามีการเปลี่ยนแปลง กรุณาเปิดประวัติอีกครั้ง"
          : err.message;
    }
  } finally {
    if (version === chatVersion) busyChat(false);
  }
}
$("query").onsubmit = (e) => {
  e.preventDefault();
  guard(sendChat);
};
for (const b of document.querySelectorAll("[data-prompt]"))
  b.onclick = () => {
    $("question").value = b.dataset.prompt;
    $("query").requestSubmit();
  };
function adminCard(title) {
  const box = el("section", undefined, "admin-card");
  box.append(el("h2", title));
  return box;
}
async function showAdmin(seq) {
  const root = $("admin-content");
  $("admin-providers").hidden = me?.role !== "admin";
  $("admin-documents").hidden = me?.role !== "admin";
  if (!me) {
    root.replaceChildren(gated("เข้าสู่ระบบด้วยบัญชีผู้ดูแล AI"));
    return;
  }
  if (me.role !== "admin") {
    root.replaceChildren(empty("บัญชีนี้ไม่มีสิทธิ์เปิดหน้าผู้ดูแล AI"));
    return;
  }
  loadProviders(seq).catch((error) => { if (seq === epoch) $("provider-result").textContent = error.message; });
  root.replaceChildren(empty("กำลังโหลด…"));
  refreshDepartments()
    .then(() => loadDocuments(seq))
    .catch((err) => {
      if (seq === epoch) loadDocuments(seq);
    });
  const data = await api("/api/admin/agent");
  if (seq !== epoch) return;
  const stats = adminCard("ผลการตอบใน 24 ชั่วโมง"),
    grid = el("div", undefined, "admin-stats");
  for (const [key, label] of [
    ["answered", "ตอบได้"],
    ["fallback", "ส่งต่อ"],
    ["refused", "ปฏิเสธ"],
  ]) {
    const cell = el("div");
    cell.append(el("strong", String(data.counts[key] || 0)), el("span", label));
    grid.append(cell);
  }
  stats.append(grid);
  const model = adminCard("โมเดลและดัชนี"),
    list = el("dl", undefined, "admin-list");
  for (const [label, value] of [
    ["โมเดล", data.model],
    ["การเชื่อมต่อ", data.protocol],
    ["รุ่นโมเดล", data.model_revision],
    ["เกณฑ์คะแนนค้นคืน", data.threshold],
    ["รุ่นดัชนี", data.revision],
  ])
    list.append(el("dt", label), el("dd", String(value || "—")));
  model.append(list);
  const tool = adminCard("เครื่องมือตรวจเคส"),
    state = data.case_status;
  const toggle = button(
    state.enabled ? "ปิดเครื่องมือตรวจเคส" : "เปิดเครื่องมือตรวจเคส",
    async () => {
      toggle.disabled = true;
      try {
        await api("/api/admin/tools/case-status", {
          enabled: !state.enabled,
          version: state.version,
        });
        if (seq === epoch) await show("admin");
      } catch (err) {
        if (seq === epoch) {
          message(err.message);
          toggle.disabled = false;
        }
      }
    },
  );
  tool.append(
    el(
      "p",
      state.enabled
        ? "พนักงานตรวจเคสของตนเองผ่านแชตได้"
        : "ปิดการตรวจเคสผ่านแชตอยู่",
    ),
    toggle,
  );
  const audit = adminCard("ประวัติการตั้งค่า");
  for (const change of data.changes)
    audit.append(
      el(
        "p",
        (change.enabled ? "เปิด" : "ปิด") +
          "เครื่องมือตรวจเคส · " +
          idName(change.actor) +
          " · " +
          date(change.at),
      ),
    );
  if (!data.changes.length)
    audit.append(el("p", "ยังไม่มีการเปลี่ยนแปลง", "muted"));
  const reasons = adminCard("สาเหตุที่บันทึกใน 24 ชั่วโมง");
  for (const item of data.reasons)
    reasons.append(el("p", item.reason + " · " + item.count));
  if (!data.reasons.length) reasons.append(el("p", "ยังไม่มีรายการ", "muted"));
  root.replaceChildren(stats, model, tool, audit, reasons);
}
$("admin-refresh").onclick = () => show("admin");
boot();
