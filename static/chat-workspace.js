"use strict";
let attachedFile = null;
let activeStream = null;
const stageLabels = {
  plan: "เลือกเครื่องมือสำหรับรายงาน / Plan report",
  aggregate: "รวมยอดจากแถวข้อมูล / Aggregate rows",
  chart: "สร้างกราฟจากยอดรวม / Build chart",
  powerpoint: "สร้าง PowerPoint / Generate PowerPoint",
  export: "เตรียมคำตอบที่เลือก / Prepare previous answer",
  access: "ตรวจสิทธิ์ / Check access",
  file: "เปิดไฟล์แนบ / Open attachment",
  lookup: "ค้นเอกสารที่คุณอ่านได้ / Search permitted sources",
  select: "เลือกข้อความอ้างอิง / Select evidence",
  calculator: "คำนวณในพื้นที่แยก / Run calculation",
  data: "อ่านแถวและคอลัมน์ / Inspect table",
  attachment: "หาข้อความในไฟล์ / Find passages",
  draft: "ส่งคำขอให้โมเดลเขียนร่าง / Generate draft",
  case_status: "ตรวจเคสของบัญชีนี้ / Read your cases",
};
const toolHints = {
  auto: "แนบ Excel แล้วขอสรุปยอด ทำกราฟ และสร้าง PowerPoint ได้ในข้อความเดียว โมเดลที่ตั้งค่าไว้จะได้รับคำขอและชื่อคอลัมน์ / For reports, your request and column names go to the configured model.",
  attachment:
    "แนบไฟล์แล้วถาม หรือพิมพ์ “สรุป” เพื่อดูข้อความตัวอย่าง / Ask about the file or enter “preview”.",
  data: "แนบ CSV หรือ Excel แล้วพิมพ์ “แสดงตาราง” / Attach a spreadsheet and enter “show table”.",
  calculator:
    'ลอง (1200 + 300) / 3 หรือ sum(col("Sales")) เมื่อแนบตาราง / Enter a formula.',
  draft:
    "ระบุสิ่งที่ต้องการเขียน โมเดลที่แอดมินเลือกจะได้รับคำขอและข้อความจากไฟล์แนบ / Your brief and attached text go to the configured chat provider.",
  case_status:
    "พิมพ์ “ดูเคสของฉัน” หรือระบุเลขเคส / Ask for your cases or enter a case reference.",
};
function updateTool() {
  $("tool-hint").textContent = toolHints[$("chat-mode").value];
  $("chat-file-chip").hidden = !attachedFile;
  $("chat-file-chip").textContent = attachedFile
    ? attachedFile.name + " ×"
    : "";
}
function clearChatTools() {
  activeStream?.abort();
  attachedFile = null;
  $("chat-file").value = "";
  $("chat-mode").value = "auto";
  updateTool();
}
$("chat-mode").onchange = updateTool;
$("pick-chat-file").onclick = () => $("chat-file").click();
$("chat-file-chip").onclick = () => {
  attachedFile = null;
  $("chat-file").value = "";
  updateTool();
};
$("chat-file").onchange = async () => {
  const file = $("chat-file").files[0];
  if (!file || sending || !me) return;
  const version = chatVersion,
    subject = me.subject;
  const id = chatId || crypto.randomUUID();
  busyChat(true);
  $("chat-progress").textContent = "กำลังอ่านไฟล์ / Reading file…";
  try {
    if (file.size > 5 * 1024 * 1024)
      throw Error("ไฟล์ต้องไม่เกิน 5 MB / Maximum 5 MB");
    const res = await fetch(
      "/api/conversations/" +
        id +
        "/attachments?name=" +
        encodeURIComponent(file.name),
      {
        method: "POST",
        headers: {
          "X-Desk-Request": "1",
          "Content-Type": "application/octet-stream",
        },
        body: file,
      },
    );
    const data = await res.json();
    if (!res.ok)
      throw Error(
        typeof data.detail === "string"
          ? data.detail
          : "อ่านไฟล์ไม่ได้ / Upload failed",
      );
    if (version !== chatVersion || subject !== me?.subject) return;
    chatId = id;
    attachedFile = data;
    retry = null;
    $("chat-progress").textContent =
      "ไฟล์พร้อมใช้เฉพาะในแชตนี้ / File ready in this chat";
    updateTool();
  } catch (error) {
    if (version === chatVersion) $("chat-progress").textContent = error.message;
  } finally {
    if (version === chatVersion) busyChat(false);
    $("chat-file").value = "";
  }
};
$("stop-chat").onclick = () => activeStream?.abort();
async function streamReply(chat, payload, signal, onFrame) {
  const res = await fetch("/api/conversations/" + chat + "/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Desk-Request": "1" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok)
    throw Error("ส่งข้อความไม่ได้ / Request failed (" + res.status + ")");
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let tail = "",
    result;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      tail += value;
      const lines = tail.split("\n");
      tail = lines.pop();
      for (const line of lines) {
        if (!line) continue;
        const frame = JSON.parse(line);
        if (frame.event === "error")
          throw Object.assign(Error(frame.value.message), {
            status: frame.value.status,
          });
        if (frame.event === "result") result = frame.value;
        else onFrame(frame);
      }
    }
  } finally {
    reader.releaseLock();
  }
  if (!result)
    throw Error(
      "การเชื่อมต่อขาดช่วง ส่งอีกครั้งได้ / Connection interrupted. Retry the same message.",
    );
  return result;
}
function renderDataset(result) {
  const area = el("div", undefined, "data-preview"),
    { headers, rows } = result.table;
  if (result.metrics?.length) {
    const field = el("label", "กราฟ 20 แถวแรก / Chart preview ");
    const select = el("select");
    for (const metric of result.metrics) select.add(new Option(metric, metric));
    const chart = el("div", undefined, "chat-chart");
    function plot() {
      const pos = headers.indexOf(select.value);
      const labelPos = headers.findIndex((_, idx) => idx !== pos);
      const points = rows
        .slice(0, 20)
        .filter((row) => row[pos].trim())
        .map((row, idx) => ({
          label: labelPos >= 0 ? row[labelPos] : String(idx + 1),
          value: Number(row[pos].replaceAll(",", "")),
        }));
      const max = Math.max(1, ...points.map((p) => Math.abs(p.value)));
      chart.replaceChildren(
        ...points.map((point) => {
          const line = el("div", undefined, "bar-row"),
            bar = el("progress");
          bar.max = max;
          bar.value = Math.abs(point.value);
          bar.setAttribute("aria-label", point.label + " " + point.value);
          if (point.value < 0) bar.classList.add("negative");
          line.append(
            el("span", point.label),
            bar,
            el("strong", point.value.toLocaleString()),
          );
          return line;
        }),
      );
    }
    select.onchange = plot;
    field.append(select);
    area.append(field, chart);
    plot();
  }
  const wrapper = el("div", undefined, "table-scroll"),
    table = el("table");
  const head = table.createTHead().insertRow();
  for (const h of headers) head.append(el("th", h));
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const v of row) tr.insertCell().textContent = v;
  }
  wrapper.tabIndex = 0;
  wrapper.append(table);
  area.append(wrapper);
  return area;
}
function responseActions(chat, turn, result) {
  const actions = el("div", undefined, "response-actions");
  const copy = button("คัดลอก / Copy", async () => {
    try {
      await navigator.clipboard.writeText(result.answer);
      copy.textContent = "คัดลอกแล้ว / Copied";
    } catch {
      copy.textContent = "เลือกข้อความเพื่อคัดลอก / Select text to copy";
    }
  });
  const format = el("select");
  format.setAttribute("aria-label", "เลือกชนิดไฟล์ / File format");
  for (const [value, label] of [
    ["pdf", "PDF"],
    ["docx", "Word (.docx)"],
    ["pptx", "PowerPoint (.pptx)"],
    ["xlsx", "Excel (.xlsx)"],
    ["csv", "CSV"],
    ["md", "Markdown"],
  ])
    format.add(new Option(label, value));
  if (result.preferred_format) format.value = result.preferred_format;
  const link = el("a", "ดาวน์โหลด / Download", "outline");
  const update = () => {
    link.href =
      "/api/conversations/" +
      chat +
      "/turns/" +
      turn +
      "/export/" +
      format.value;
    link.download = "answer." + format.value;
    if (result.artifact) link.textContent = format.value===result.artifact.format ? "ดาวน์โหลด PowerPoint / Download slides" : "ดาวน์โหลด / Download";
  };
  format.onchange = update;
  update();
  actions.append(copy, format, link);
  return actions;
}
updateTool();
