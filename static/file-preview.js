"use strict";
const filePreview = $("file-preview");
const previewBody = $("file-preview-body");
let previewRequest;

function filePreviewButton(chat, file) {
  const type = file.name.split(".").at(-1).toUpperCase();
  const opener = button(
    "",
    () => {
      const url = "/api/conversations/" + chat + "/attachments/" + file.id;
      showFilePreview(file.name, url + "/preview", url + "/thumbnail");
    },
    "message-file",
  );
  opener.append(
    icon("file-text"),
    el("span", file.name),
    el("small", "ดู " + type),
  );
  opener.title = "ดูตัวอย่าง " + file.name;
  return opener;
}

function previewTable(headers, rows) {
  return renderDataset({ table: { headers, rows }, metrics: [] });
}

function previewChart(points) {
  const chart = el("div", undefined, "slide-chart");
  const max = Math.max(1, ...points.map((point) => Math.abs(point.value)));
  for (const { label, value } of points) {
    const row = el("div", undefined, "bar-row");
    const bar = el("progress");
    bar.max = max;
    bar.value = Math.abs(value);
    bar.setAttribute("aria-label", label + " " + value);
    if (value < 0) bar.classList.add("negative");
    row.append(
      el("span", label),
      bar,
      el("strong", Number(value).toLocaleString("th-TH")),
    );
    chart.append(row);
  }
  return chart;
}

function renderSlides(slides) {
  const slide = el("section", undefined, "preview-slide");
  const toolbar = el("nav", undefined, "preview-pager");
  toolbar.setAttribute("aria-label", "เปลี่ยนสไลด์");
  const count = el("span");
  count.setAttribute("aria-live", "polite");
  let index = 0;
  const prev = button("ก่อนหน้า", () => {
    index--;
    render();
  });
  const next = button("ถัดไป", () => {
    index++;
    render();
  });
  function render() {
    const page = slides[index];
    slide.replaceChildren(el("h3", page.title));
    for (const line of page.text) slide.append(el("p", line));
    if (page.points.length) slide.append(previewChart(page.points));
    if (page.table?.length)
      slide.append(previewTable(page.table[0], page.table.slice(1)));
    if (page.source) slide.append(el("small", page.source, "preview-source"));
    count.textContent = "สไลด์ " + (index + 1) + " จาก " + slides.length;
    prev.disabled = index === 0;
    next.disabled = index === slides.length - 1;
  }
  toolbar.append(prev, count, next);
  previewBody.append(slide, toolbar);
  render();
}

async function showFilePreview(name, url, thumbnail, download) {
  previewRequest?.abort();
  const current = new AbortController();
  previewRequest = current;
  $("file-preview-title").textContent = name;
  $("file-preview-download").hidden = !download;
  if (download) $("file-preview-download").href = download;
  previewBody.replaceChildren(el("p", "กำลังเปิดตัวอย่างไฟล์…", "meta"));
  if (!filePreview.open) filePreview.showModal();
  try {
    const response = await fetch(url, { signal: current.signal });
    if (response.status === 404)
      throw Error("ไฟล์นี้หมดอายุหรือเปิดด้วยบัญชีนี้ไม่ได้");
    if (!response.ok) throw Error("เปิดตัวอย่างไฟล์ไม่ได้ กรุณาลองใหม่");
    const data = await response.json();
    if (current.signal.aborted) return;
    previewBody.replaceChildren();
    if (data.kind === "table") {
      previewBody.append(
        el(
          "p",
          "แสดง " +
            data.rows.length +
            " จาก " +
            data.row_count +
            " แถวในชีตแรก",
          "meta",
        ),
        previewTable(data.headers, data.rows),
      );
    } else if (data.kind === "slides") {
      renderSlides(data.slides);
    } else {
      if (data.image) {
        const page = el("img", undefined, "preview-page");
        page.alt = "หน้าแรกของ " + name;
        page.src = thumbnail;
        previewBody.append(page);
      }
      const content = el(
        data.image ? "details" : "section",
        undefined,
        "preview-extract",
      );
      content.append(
        el(data.image ? "summary" : "h3", "ข้อความที่อ่านได้จากไฟล์"),
      );
      for (const { label, text } of data.sections) {
        content.append(el("h4", label), el("p", text));
      }
      previewBody.append(content);
    }
  } catch (error) {
    if (!current.signal.aborted)
      previewBody.replaceChildren(el("p", error.message, "notice"));
  }
}
$("file-preview-close").onclick = () => filePreview.close();
filePreview.addEventListener("close", () => {
  if (filePreview.open) return;
  previewRequest?.abort();
  previewBody.replaceChildren();
  $("file-preview-download").removeAttribute("href");
});
