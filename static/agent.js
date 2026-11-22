"use strict";
let chatId = null,
  lastTurn = null,
  retry = null,
  chatVersion = 0,
  sending = false;
const chatStorage = () => me && "folio.chat." + me.subject + "." + me.role;
function busyChat(value) {
  sending = value;
  for (const name of [
    "submitq",
    "question",
    "new-chat",
    "chat-mode",
    "pick-chat-file",
    "chat-file-chip",
  ])
    $(name).disabled = value;
  for (const b of document.querySelectorAll(
    "#chat-history button, [data-prompt]",
  ))
    b.disabled = value;
}
function resetChat() {
  if (me) sessionStorage.removeItem(chatStorage());
  chatVersion++;
  clearChatTools();
  chatId = lastTurn = retry = null;
  $("result").replaceChildren();
  markCurrentChat();
  $("question").value = $("chat-progress").textContent = "";
  busyChat(false);
}
function renderTurn(question, r, chat, turnId, steps = []) {
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
          : {
              sheet_report: "รายงานจากไฟล์แนบ / Spreadsheet report",
              export: "พร้อมดาวน์โหลด / Ready to export",
              draft: "ร่างเอกสาร / Draft",
              calculator: "ผลคำนวณ / Calculation",
              data: "ข้อมูลในไฟล์ / File data",
              attachment: "ข้อความในไฟล์ / File passages",
            }[r.tool] || "คำตอบจากเอกสาร";
  reply.append(el("strong", label), el("p", r.answer, "answer"));
  if (r.used_context)
    reply.append(
      el(
        "small",
        r.tool === "case_status"
          ? "ใช้เลขเคสจากคำตอบก่อนหน้า"
          : "ใช้คำตอบก่อนหน้า / Used previous response",
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
    el("summary", "ที่มาและขั้นตอน / Sources & steps"),
    el("small", "Trace ID · " + r.trace_id, "meta"),
  );
  if (r.planner) info.append(el("p",r.planner==="model" ? "โมเดลเลือกเครื่องมือ / Model-selected tools" : "ใช้กฎพื้นฐาน ยังไม่ได้เชื่อมโมเดล / Built-in rules, no chat model configured","meta"));
  const usedStages = r.steps || steps;
  if (usedStages.length)
    info.append(
      el(
        "p",
        usedStages
          .map((code) => stageLabels[code])
          .filter(Boolean)
          .join(" → "),
        "meta",
      ),
    );
  if (r.table) reply.append(renderDataset(r));
  reply.append(info);
  if (chat && turnId) reply.append(responseActions(chat, turnId, r));
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
  clearChatTools();
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
      ...rows.map((r) => renderTurn(r.question, r.result, id, r.id)),
    );
    attachedFile = rows.at(-1)?.result.attachment || null;
    updateTool();
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
  const mode = $("chat-mode").value,
    file = attachedFile?.id || null;
  if (
    !retry ||
    retry.question !== question ||
    retry.mode !== mode ||
    retry.attachment_id !== file
  )
    retry = {
      chat: chatId || crypto.randomUUID(),
      turn_id: crypto.randomUUID(),
      parent_id: lastTurn,
      mode,
      attachment_id: file,
      question,
    };
  const { chat, ...payload } = retry;
  busyChat(true);
  message("");
  const controller = new AbortController();
  activeStream = controller;
  $("stop-chat").hidden = false;
  const draft = el("article", undefined, "chat-turn");
  const userBox = el("div", undefined, "user-message");
  userBox.append(el("small", "คุณ / You", "meta"), el("p", question));
  const live = el("div", undefined, "agent-message"),
    text = el("p", "", "answer");
  live.append(text);
  draft.append(userBox, live);
  $("result").append(draft);
  const steps = [];
  try {
    const r = await streamReply(chat, payload, controller.signal, (frame) => {
      if (version !== chatVersion) return;
      if (frame.event === "delta")
        text.append(document.createTextNode(frame.value));
      if (frame.event === "stage") {
        steps.push(frame.value);
        $("chat-progress").textContent = stageLabels[frame.value] || "";
      }
      $("result").scrollTop = $("result").scrollHeight;
    });
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
    const node = renderTurn(question, r, chat, lastTurn, steps);
    draft.replaceWith(node);
    if (!payload.parent_id)
      api("/api/conversations/" + chat + "/title", {})
        .then(({ title }) => {
          if (me?.subject !== subject) return;
          const item = Array.from(
            $("chat-history").querySelectorAll("button"),
          ).find((b) => b.dataset.id === chat);
          if (item) {
            item.title = title;
            item.querySelector("span").textContent = title;
          }
        })
        .catch(() => {});
    $("question").value = "";
    $("chat-progress").textContent = "";
    if (view === "ask") {
      $("result").scrollTop = $("result").scrollHeight;
    }
  } catch (err) {
    if (version === chatVersion && me?.subject === subject) {
      $("chat-progress").textContent =
        err.name === "AbortError"
          ? "หยุดแล้ว / Stopped"
          : err.status === 409
            ? "บทสนทนามีการเปลี่ยนแปลง กรุณาเปิดประวัติอีกครั้ง"
            : err.message;
    }
  } finally {
    draft.remove();
    if (activeStream === controller) {
      activeStream = null;
      $("stop-chat").hidden = true;
    }
    if (version === chatVersion) busyChat(false);
  }
}
$("query").onsubmit = (e) => {
  e.preventDefault();
  guard(sendChat);
};
for (const b of document.querySelectorAll("[data-prompt]"))
  b.onclick = () => {
    attachedFile = null;
    $("chat-mode").value = "auto";
    updateTool();
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
  const permitted = me?.role === "admin";
  $("admin-steps").hidden = $("admin-operations").hidden = !permitted;
  $("admin-gate").textContent = permitted
    ? ""
    : "เฉพาะผู้ดูแล / Admin access required";
  $("admin-providers").hidden = me?.role !== "admin";
  $("admin-documents").hidden = me?.role !== "admin";
  if (!me) {
    $("admin-gate").replaceChildren(gated("เข้าสู่ระบบด้วยบัญชีผู้ดูแล / Sign in as an admin"));
    return;
  }
  if (me.role !== "admin") {
    root.replaceChildren(empty("บัญชีนี้ไม่มีสิทธิ์เปิดหน้าผู้ดูแล AI"));
    return;
  }
  $("provider-add").disabled = true;
  loadProviders(seq).catch((error) => {
    if (seq === epoch) $("provider-result").textContent = error.message;
  });
  root.replaceChildren(empty("กำลังโหลด…"));
  refreshDepartments()
    .then(() => loadDocuments(seq))
    .catch((err) => {
      if (seq === epoch) loadDocuments(seq);
    });
  const data = await api("/api/admin/agent");
  if (seq !== epoch) return;
  const stats = adminCard("ผลการตอบใน 24 ชั่วโมง / Last 24 hours"),
    grid = el("div", undefined, "admin-stats");
  for (const [key, label] of [
    ["answered", "ตอบได้ / Answered"],
    ["fallback", "ส่งต่อ / Referred"],
    ["refused", "ปฏิเสธ / Declined"],
  ]) {
    const cell = el("div");
    cell.append(el("strong", String(data.counts[key] || 0)), el("span", label));
    grid.append(cell);
  }
  stats.append(grid);
  const model = adminCard("การตั้งค่าทางเทคนิค / Technical settings"),
    list = el("dl", undefined, "admin-list");
  for (const [label, value] of [
    ["โมเดล / Model", data.model],
    ["การเชื่อมต่อ / Connection", data.protocol],
    ["รุ่นโมเดล / Version", data.model_revision],
    ["คะแนนขั้นต่ำที่ใช้ตอบ / Minimum evidence score", data.threshold],
    ["ชุดเอกสารที่ใช้อยู่ / Library version", data.revision],
  ])
    list.append(el("dt", label), el("dd", String(value || "—")));
  model.append(list);
  const tool = adminCard("เครื่องมือตรวจเคส"),
    state = data.case_status;
  const toggle = button(
    state.enabled
      ? "ปิดการตรวจเคส / Disable lookup"
      : "เปิดการตรวจเคส / Enable lookup",
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
        ? "พนักงานดูเคสของตัวเองในแชตได้ / Employees can check their cases in chat"
        : "ปิดการตรวจเคสในแชต / Case lookup is off",
    ),
    toggle,
  );
  const audit = adminCard("ประวัติการตั้งค่า / Change history");
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
    audit.append(el("p", "ยังไม่มีการเปลี่ยนแปลง / No changes", "muted"));
  const reasons = adminCard("เหตุผลของคำตอบ / Response reasons");
  for (const item of data.reasons)
    reasons.append(el("p", item.reason + " · " + item.count));
  if (!data.reasons.length)
    reasons.append(el("p", "ยังไม่มีรายการ / No entries", "muted"));
  root.replaceChildren(stats, model, tool, audit, reasons);
}
$("admin-refresh").onclick = () => show("admin");
$("step-connect").onclick = () => $("provider-add").focus();
$("step-upload").onclick = () => $("pdf-file").focus();
$("step-test").onclick = () => go("ask");
boot();
