"use strict";
let attachedFile = null;
let activeStream = null;
const stageLabels = {
  policy_email: "จัดทำร่างอีเมลจากหลักฐาน / Draft email from evidence",
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
function updateAttachment() {
  $("chat-file-chip").hidden = !attachedFile;
  const slot = $("chat-file-link");
  slot.replaceChildren();
  if (attachedFile) slot.append(filePreviewButton(chatId, attachedFile));
}
function clearChatTools() {
  activeStream?.abort();
  if (filePreview.open) filePreview.close();
  previewRequest?.abort();
  attachedFile = null;
  $("chat-file").value = "";
  updateAttachment();
}
$("question").onkeydown = (event) => {
  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.isComposing &&
    event.keyCode !== 229
  ) {
    event.preventDefault();
    if (!sending) $("query").requestSubmit();
  }
};
$("pick-chat-file").onclick = () => guard(() => $("chat-file").click());
$("chat-file-remove").onclick = () => {
  attachedFile = null;
  $("chat-file").value = "";
  updateAttachment();
};
const samples = {
  sales: [
    "sales-q1.xlsx",
    "ช่วยสรุปยอดขาย ทำกราฟ แล้วทำเป็น PowerPoint ให้หน่อย",
  ],
  training: ["training-day.pdf", "สรุปกำหนดการอบรมและเวลาแต่ละกิจกรรม"],
  notes: ["support-notes.txt", "สรุปบทสนทนาเรื่องใบเสร็จและสิ่งที่ต้องติดตาม"],
};
for (const card of document.querySelectorAll("[data-sample]")) {
  card.onclick = () =>
    guard(() => {
      const [name, prompt] = samples[card.dataset.sample];
      return attachToChat(async () => {
        const res = await fetch("/static/samples/" + name);
        if (!res.ok) throw Error("เปิดไฟล์ตัวอย่างไม่ได้ กรุณาลองอีกครั้ง");
        return new File([await res.blob()], name);
      }, prompt);
    });
}
$("chat-file").onchange = () => {
  const file = $("chat-file").files[0];
  if (file) return attachToChat(() => file);
};
function attachDropped(files) {
  if (!files.length) return;
  if (files.length !== 1) {
    $("chat-progress").textContent =
      "เลือกไฟล์ครั้งละ 1 ไฟล์ / Choose one file at a time";
    return;
  }
  const file = files[0];
  guard(() => attachToChat(() => file));
}
const composer = $("query");
composer.addEventListener("dragover", (event) => {
  if (event.dataTransfer.types.includes("Files")) {
    event.preventDefault();
    composer.classList.add("dragging");
  }
});
composer.addEventListener("dragleave", (event) => {
  if (!composer.contains(event.relatedTarget))
    composer.classList.remove("dragging");
});
composer.addEventListener("drop", (event) => {
  composer.classList.remove("dragging");
  if (event.dataTransfer.files.length) {
    event.preventDefault();
    attachDropped(event.dataTransfer.files);
  }
});
$("question").addEventListener("paste", (event) => {
  if (event.clipboardData?.files.length) {
    event.preventDefault();
    attachDropped(event.clipboardData.files);
  }
});
async function attachToChat(loadFile, prompt) {
  if (sending || !me) return;
  const version = chatVersion,
    subject = me.subject;
  const id = chatId || crypto.randomUUID();
  busyChat(true);
  $("chat-progress").textContent = "กำลังอ่านไฟล์ / Reading file…";
  try {
    const file = await loadFile();
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
    if (prompt) {
      const current = $("question").value.trim();
      if (
        !current ||
        Object.values(samples).some(([, previous]) => previous === current)
      )
        $("question").value = prompt;
    }
    updateAttachment();
  } catch (error) {
    if (version === chatVersion) $("chat-progress").textContent = error.message;
  } finally {
    if (version === chatVersion) {
      busyChat(false);
      $("question").focus();
    }
    $("chat-file").value = "";
  }
}
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
function reportCard(result) {
  const { summary, chart } = result;
  const card = el("section", undefined, "report-card");
  const heading = el("div", undefined, "report-heading");
  heading.append(
    icon("clipboard-list"),
    el("strong", result.report_title || summary.metric),
  );
  const body = el("div", undefined, "report-body");
  const value = el("div", undefined, "report-total");
  value.append(
    el("span", "ยอดรวม", "meta"),
    el(
      "b",
      Number(summary.total).toLocaleString("th-TH", {
        maximumFractionDigits: 6,
      }),
    ),
    el("small", summary.metric + " · " + summary.records + " รายการ"),
  );
  body.append(value);
  if (chart?.points?.length) {
    const plot = el("div", undefined, "report-bars");
    plot.setAttribute("aria-label", chart.metric + " ตาม" + chart.category);
    const max = Math.max(
      ...chart.points.map((point) => Math.abs(point.value)),
      1,
    );
    for (const point of chart.points) {
      const row = el("div", undefined, "bar-row");
      const meter = el("progress");
      meter.max = max;
      meter.value = Math.abs(point.value);
      meter.setAttribute("aria-label", point.label + " " + point.value);
      if (point.value < 0) meter.classList.add("negative");
      row.append(
        el("span", point.label),
        meter,
        el(
          "strong",
          point.value.toLocaleString("th-TH", { maximumFractionDigits: 6 }),
        ),
      );
      plot.append(row);
    }
    if (chart.points.length < chart.groups)
      plot.append(
        el(
          "small",
          "แสดง " + chart.points.length + " จาก " + chart.groups + " กลุ่ม",
          "meta",
        ),
      );
    body.append(plot);
  }
  const detail = el("details", undefined, "report-table");
  detail.append(
    el("summary", "ดูตารางข้อมูล · " + result.table.rows.length + " แถว"),
    renderDataset({ ...result, metrics: [] }),
  );
  card.append(heading, body, detail);
  return card;
}
function slideDownload(chat, turn, result) {
  const url = "/api/conversations/" + chat + "/turns/" + turn + "/export/pptx";
  const filename = (result.report_title || "รายงาน") + ".pptx";
  const card = el("div", undefined, "slide-download");
  const opener = button(
    "",
    () => showFilePreview(filename, url + "?preview=true", null, url),
    "slide-opener",
  );
  const thumb = el("span", undefined, "slide-thumb");
  thumb.append(icon("clipboard-list"), el("span", "PPTX"));
  const name = el("span", undefined, "slide-filename");
  name.append(
    el("strong", filename),
    el(
      "small",
      "ดูตัวอย่าง PowerPoint · " +
        Math.ceil(result.artifact.bytes / 1024) +
        " KB",
    ),
  );
  opener.append(thumb, name);
  const link = el("a", undefined, "slide-action");
  link.append(icon("arrow-up-right"), el("span", "ดาวน์โหลด"));
  link.href = url;
  link.download = filename;
  link.setAttribute("aria-label", "ดาวน์โหลด " + filename);
  card.append(opener, link);
  return card;
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
    if (result.artifact)
      link.textContent =
        format.value === result.artifact.format
          ? "ดาวน์โหลด PowerPoint / Download slides"
          : "ดาวน์โหลด / Download";
  };
  format.onchange = update;
  update();
  const menu = el("details", undefined, "export-menu");
  const trigger = el("summary", "ส่งออก");
  const options = el("div", undefined, "export-options");
  options.append(format, link);
  menu.append(trigger, options);
  actions.append(copy, menu);
  return actions;
}
