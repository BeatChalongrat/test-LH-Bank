"use strict";
const docRoles = {
  employee: "พนักงาน / Employee",
  reviewer: "ฝ่ายสนับสนุน / Support",
  executive: "ผู้บริหาร / Executive",
  admin: "ผู้ดูแล / Admin",
};
const uploadErrors = {
  encrypted_pdf: "PDF มีการเข้ารหัส กรุณาอัปโหลดฉบับที่เปิดอ่านได้",
  page_limit: "เอกสารต้องมี 1–80 หน้า",
  text_limit: "เอกสารมีข้อความมากเกินขีดจำกัด กรุณาแบ่งไฟล์เป็นส่วน",
  empty_pdf: "ไม่พบข้อความสำหรับทำดัชนี",
  unreadable_pdf:
    "อ่านข้อความจาก PDF ไม่ได้ กรุณาตรวจไฟล์และความชัดของหน้าสแกน",
  unreadable_file:
    "อ่านไฟล์ไม่ได้ กรุณาตรวจชนิดไฟล์ การเข้ารหัส และความชัดของหน้าสแกน",
  file_limit: "ขนาดเนื้อหาภายในไฟล์เกินขีดจำกัด",
  processing_timeout: "อ่านไฟล์เกินเวลาที่กำหนด กรุณาแบ่งไฟล์เป็นส่วน",
  index_failed: "เตรียมเอกสารไม่สำเร็จ / Processing failed กรุณาให้ผู้ดูแลระบบตรวจบันทึกของ indexer",
};
const documentPageSize = 30;
let documentOffset = 0;
let documentRequest = 0;
let documentTimer;
let editingDocument;

for (const field of document.querySelectorAll(".role-options")) {
  for (const [role, label] of Object.entries(docRoles)) {
    const node = el("label");
    const check = el("input");
    check.type = "checkbox";
    check.name = "document-role";
    check.value = role;
    node.append(check, document.createTextNode(label));
    field.append(node);
  }
}

function readersOf(form) {
  const checked = (name) =>
    Array.from(
      form.querySelectorAll('[name="' + name + '"]:checked'),
      (input) => input.value,
    );
  return {
    roles: checked("document-role"),
    departments: checked("reader-department"),
    people: Array.from(readerFields.get(form).entries.keys()),
  };
}

function hasReaders(readers) {
  return Object.values(readers).some((values) => values.length);
}

function resetDocuments() {
  clearTimeout(documentTimer);
  documentRequest++;
  documentOffset = 0;
  editingDocument = undefined;
  resetDirectory();
  $("admin-documents").hidden = true;
  $("managed-list").replaceChildren();
  $("pdf-upload").reset();
  $("upload-status").textContent = "";
  $("publication-note").textContent = "";
  if ($("upload-dialog").open) $("upload-dialog").close();
  if ($("access-dialog").open) $("access-dialog").close();
}

function editDocument(doc) {
  editingDocument = doc;
  const form = $("document-access");
  for (const input of form.querySelectorAll('[name="document-role"]'))
    input.checked = doc.roles.includes(input.value);
  const fields = readerFields.get(form);
  fields.reset(doc.people);
  for (const input of fields.departments.querySelectorAll("input"))
    input.checked = doc.departments.includes(input.value);
  $("pdf-enabled").checked = doc.enabled;
  $("access-document").textContent = doc.title;
  $("access-status").textContent = "";
  $("access-dialog").showModal();
}

function documentRow(doc) {
  const article = el("article", undefined, "managed-doc");
  const header = el("div", undefined, "managed-doc-title");
  const states = {
    queued: "รอเตรียมเอกสาร / Queued",
    indexing: "กำลังเตรียมเอกสาร / Preparing",
    ready: doc.enabled ? "ถามในแชตได้แล้ว / Ready" : "ปิดการเผยแพร่ / Unpublished",
    failed: "เตรียมเอกสารไม่สำเร็จ / Processing failed",
  };
  const status = el(
    "span",
    states[doc.status],
    "status " + (doc.status === "ready" && doc.enabled ? "resolved" : ""),
  );
  header.append(el("strong", doc.title), status);
  article.append(header, el("p", doc.filename + " · " + doc.owner, "meta"));
  article.append(
    el(
      "p",
      [
        ...doc.roles.map((role) => docRoles[role]),
        ...doc.departments.map(
          (code) =>
            departmentList.find((dept) => dept.code === code)?.name || code,
        ),
        ...doc.people,
      ].join(" · "),
      "document-readers",
    ),
  );
  if (doc.error)
    article.append(
      el("p", uploadErrors[doc.error] || uploadErrors.index_failed, "meta"),
    );
  if (doc.status === "ready") {
    const footer = el("div", undefined, "managed-doc-actions");
    footer.append(
      el(
        "small",
        "อัปเดต " + date(doc.updated) + " · " + idName(doc.actor),
        "meta",
      ),
      button("แก้ไขสิทธิ์ / Edit access", () => editDocument(doc)),
    );
    article.append(footer);
  }
  return article;
}

async function loadDocuments(seq = epoch) {
  clearTimeout(documentTimer);
  if (seq !== epoch || view !== "admin" || me?.role !== "admin") return;
  const currentRequest = ++documentRequest;
  try {
    const docs = await api("/api/admin/documents?offset=" + documentOffset);
    if (seq !== epoch || currentRequest !== documentRequest) return;
    const rows = docs.slice(0, documentPageSize);
    $("managed-list").replaceChildren(
      ...(rows.length
        ? rows.map(documentRow)
        : [empty("ยังไม่มีเอกสาร / No uploaded documents")]),
    );
    $("documents-prev").hidden = !documentOffset;
    $("documents-next").hidden = docs.length <= documentPageSize;
    if (
      rows.some((doc) => doc.status === "queued" || doc.status === "indexing")
    )
      documentTimer = setTimeout(() => loadDocuments(seq), 2400);
    return true;
  } catch (err) {
    if (seq === epoch && currentRequest === documentRequest)
      $("managed-list").replaceChildren(empty(err.message));
  }
}
$("documents-prev").onclick = () => {
  documentOffset = Math.max(0, documentOffset - documentPageSize);
  loadDocuments();
};
$("documents-next").onclick = () => {
  documentOffset += documentPageSize;
  loadDocuments();
};

$("pdf-file").onchange = (event) => {
  const selected = $("selected-file");
  selected.value = event.target.files[0]?.name || selected.defaultValue;
};

$("pdf-upload").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget,
    submit = form.querySelector("button[type=submit]"),
    status = $("upload-status"),
    file = $("pdf-file").files[0],
    readers = readersOf(form);
  if (!hasReaders(readers)) {
    status.textContent = "เลือกผู้ที่เห็นเอกสารอย่างน้อยหนึ่งรายการ / Select at least one reader or group";
    return;
  }
  if (
    !file ||
    !/[.](pdf|docx|xlsx|pptx|txt|md|csv)$/i.test(file.name) ||
    file.size > 15 * 1024 * 1024
  ) {
    status.textContent = "เลือกไฟล์ที่รองรับ ขนาดสูงสุด 15 MB / Choose a supported file up to 15 MB";
    return;
  }
  const metadata = {
    title: $("pdf-title").value.trim(),
    owner: $("pdf-owner").value.trim(),
    filename: file.name,
    ...readers,
  };
  const subject = me?.subject;
  submit.disabled = true;
  status.textContent = "กำลังอัปโหลด / Uploading…";
  try {
    const response = await fetch("/api/admin/documents", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Desk-Request": "1",
        "X-Document-Metadata": encodeURIComponent(JSON.stringify(metadata)),
      },
      body: file,
    });
    if (!response.ok) {
      const detail = (await response.json().catch(() => null))?.detail;
      throw new Error(
        typeof detail === "string"
          ? detail
          : "อัปโหลดไม่สำเร็จ กรุณาลองอีกครั้ง",
      );
    }
    if (me?.subject !== subject) return;
    form.reset();
    readerFields.get(form).reset();
    $("publication-note").textContent = "รับไฟล์ " + file.name + " แล้ว ติดตามสถานะได้ในรายการนี้";
    status.textContent = "";
    $("upload-dialog").close();
    $("admin-documents").scrollIntoView({ block: "start" });
    documentOffset = 0;
    await loadDocuments();
  } catch (err) {
    if (me?.subject === subject) status.textContent = err.message;
  } finally {
    submit.disabled = false;
  }
};

$("document-access").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget,
    doc = editingDocument,
    status = $("access-status");
  const readers = readersOf(form),
    submit = form.querySelector("button[type=submit]");
  if (!hasReaders(readers)) {
    status.textContent = "เลือกผู้ที่เห็นเอกสารอย่างน้อยหนึ่งรายการ / Select at least one reader or group";
    return;
  }
  submit.disabled = true;
  try {
    await api("/api/admin/documents/" + doc.id + "/access", {
      ...readers,
      enabled: $("pdf-enabled").checked,
      version: doc.version,
    });
    if ($("access-dialog").open) $("access-dialog").close();
    await loadDocuments();
  } catch (err) {
    status.textContent =
      err.status === 409
        ? "รายการถูกเปลี่ยนแล้ว กรุณาปิดและเปิดรายการอีกครั้ง"
        : err.message;
    if (err.status === 409) await loadDocuments();
  } finally {
    submit.disabled = false;
  }
};
