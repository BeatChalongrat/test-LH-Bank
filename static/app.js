"use strict";
const $ = (id) => document.getElementById(id);
const roleName = {
  admin: "ผู้ดูแลระบบ AI",
  employee: "พนักงาน",
  reviewer: "เจ้าหน้าที่ดูแล",
  executive: "ผู้บริหาร",
};
const stateName = {
  open: "รอรับเรื่อง",
  in_progress: "กำลังดำเนินการ",
  resolved: "ปิดรายการแล้ว",
};
const views = ["home", "ask", "library", "queue", "admin"];
const NS = "http://www.w3.org/2000/svg";
let me = null,
  auth = {},
  pending = null,
  epoch = 0,
  view = "home";
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function icon(name, cls = "") {
  const svg = document.createElementNS(NS, "svg"),
    use = document.createElementNS(NS, "use");
  svg.setAttribute("class", "icon " + cls);
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "/static/assets/icons.svg#" + name);
  svg.append(use);
  return svg;
}
for (const node of document.querySelectorAll("[data-icon]"))
  node.replaceWith(icon(node.dataset.icon, node.className));
function button(text, action, cls = "outline") {
  const b = el("button", text, cls);
  b.type = "button";
  b.addEventListener("click", action);
  return b;
}
function empty(text) {
  return el("div", text, "notice");
}
function message(text) {
  $("error").textContent = text;
}
function date(ts) {
  return new Date(ts * 1000).toLocaleString("th-TH", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
function idName(id) {
  return id?.split("/").slice(1).join("/") || id || "";
}
function clearIdentity() {
  resetChat();
  $("admin-link").hidden = true;
  $("admin-content").replaceChildren();
  resetDocuments();
  me = null;
  epoch++;
  pending = null;
  for (const id of [
    "result",
    "documents",
    "tickets",
    "home-docs",
    "case-summary",
    "doc-detail",
  ])
    $(id).replaceChildren();
  for (const id of ["doc-dialog", "auth-dialog"]) if ($(id).open) $(id).close();
  $("submitq").disabled = false;
  $("chat-history").replaceChildren();
  $("question").value = "";
  $("doc-q").value = "";
  $("identity").textContent = "เข้าสู่ระบบ";
}
async function api(path, data, required = true) {
  const r = await fetch(path, {
    method: data === undefined ? "GET" : "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers:
      data === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Desk-Request": "1" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (!r.ok) {
    if (r.status === 401 && required && me) {
      clearIdentity();
      show(view);
    }
    const errors = {
      401: "กรุณาเข้าสู่ระบบอีกครั้ง หรือขอรหัสเชิญใหม่จากผู้ดูแล",
      403: "บัญชีนี้ไม่มีสิทธิ์ทำรายการ",
      404: "ไม่พบรายการที่คุณเปิดได้",
      409: "สถานะงานเปลี่ยนแล้ว กรุณารีเฟรชรายการ",
      422: "กรุณาตรวจสอบข้อมูลที่กรอก",
      503: "บริการยังไม่พร้อม กรุณาลองอีกครั้ง",
    };
    let providerError;
    if (path.startsWith("/api/admin/provider")) {
      const failure = await r.json().catch(() => ({}));
      if (typeof failure.detail === "string") providerError = failure.detail;
    }
    const err = new Error(
      providerError || errors[r.status] || "เชื่อมต่อบริการไม่สำเร็จ กรุณาลองอีกครั้ง",
    );
    err.status = r.status;
    throw err;
  }
  return r.json();
}
function account(action) {
  pending = action || null;
  $("login-error").textContent = "";
  $("auth-title").textContent = me ? "บัญชีผู้ใช้" : "เข้าสู่พื้นที่ทำงาน";
  $("auth-note").textContent = me
    ? idName(me.subject) + " · " + roleName[me.role]
    : auth.organization
      ? "ใช้บัญชีพนักงานที่องค์กรกำหนดสิทธิ์ให้"
      : "ใส่รหัสใช้ครั้งเดียวที่ผู้ดูแลออกให้บัญชีของคุณ";
  if (!me && !auth.organization && !auth.invitation)
    $("auth-note").textContent =
      "กรุณาติดต่อผู้ดูแลเพื่อตั้งค่าช่องทางเข้าสู่ระบบ";
  $("sso").hidden = Boolean(me) || !auth.organization;
  $("invite-form").hidden = Boolean(me) || !auth.invitation;
  $("logout").hidden = !me;
  $("invite").value = "";
  if (!$("auth-dialog").open) $("auth-dialog").showModal();
}
async function signed(user) {
  me = user;
  $("identity").textContent = idName(user.subject);
  if ($("auth-dialog").open) $("auth-dialog").close();
  const action = pending;
  pending = null;
  await show(view);
  if (action) await action();
}
$("account").onclick = () => account();
$("invite-form").onsubmit = async (e) => {
  e.preventDefault();
  const b = e.currentTarget.querySelector("button");
  b.disabled = true;
  try {
    await signed(
      await api("/api/auth/invite", { code: $("invite").value.trim() }, false),
    );
  } catch (err) {
    $("login-error").textContent = err.message;
  } finally {
    b.disabled = false;
  }
};
$("logout").onclick = async () => {
  try {
    await api("/api/auth/logout", {});
    clearIdentity();
    location.replace("/");
  } catch (err) {
    $("login-error").textContent = err.message;
  }
};
for (const d of document.querySelectorAll("dialog")) {
  d.querySelector(".close").onclick = () => d.close();
  d.addEventListener("click", (e) => {
    if (
      e.target === d &&
      (e.clientX < d.getBoundingClientRect().left ||
        e.clientX > d.getBoundingClientRect().right ||
        e.clientY < d.getBoundingClientRect().top ||
        e.clientY > d.getBoundingClientRect().bottom)
    )
      d.close();
  });
}
function guard(action) {
  return me ? action() : account(action);
}
function gated(hint) {
  const n = empty(hint);
  n.append(
    document.createElement("br"),
    button("เข้าสู่ระบบ", () => account(), "light empty-action"),
  );
  return n;
}
function meta(s) {
  return [s.owner, "ฉบับ " + s.version, s.location, s.ref]
    .filter(Boolean)
    .join(" · ");
}
function detail(s) {
  $("doc-detail").replaceChildren(
    el("h2", s.title),
    el("p", meta(s), "meta"),
    el("p", s.body, "doc-body"),
  );
  if (s.original || s.pdf) $("doc-detail").append(pdfLink(s.doc_id));
  $("doc-dialog").showModal();
}
function docRow(s) {
  const b = button("", () => detail(s), "doc-row"),
    text = el("span", undefined, "doc-copy");
  text.append(
    el("strong", s.title),
    el("small", s.owner + " · " + s.doc_id + " · " + s.location),
  );
  b.append(
    icon(
      s.kind === "chat" ? "messages-square" : "file-text",
      "icon-box " + (s.kind === "chat" ? "violet" : "blue"),
    ),
    text,
    icon("chevron-right"),
  );
  return b;
}
async function home(seq) {
  $("support-hint").textContent =
    me && ["reviewer", "executive"].includes(me.role)
      ? "รายการที่อยู่ในขอบเขตงานของคุณ"
      : "สถานะคำถามที่ส่งให้เจ้าหน้าที่ตรวจสอบ";
  if (!me) {
    $("home-docs").replaceChildren(
      gated("เข้าสู่ระบบเพื่อเปิดเอกสารตามสิทธิ์ของคุณ"),
    );
    $("case-summary").replaceChildren(empty("เข้าสู่ระบบเพื่อดูรายการของคุณ"));
    return;
  }
  const [stats, docs] = await Promise.all([
    api("/api/workspace"),
    api("/api/sources?kind=policy&limit=4"),
  ]);
  if (seq !== epoch) return;
  $("home-docs").replaceChildren(
    ...(docs.length ? docs.map(docRow) : [empty("ยังไม่มีเอกสารที่เผยแพร่")]),
  );
  const entries = Object.keys(stateName).map((state, i) => {
    const row = el("div", undefined, "count-row"),
      l = el("span");
    l.append(
      icon(["inbox", "clock", "circle-check"][i]),
      document.createTextNode(stateName[state]),
    );
    row.append(l, el("strong", String(stats[state])));
    return row;
  });
  $("case-summary").replaceChildren(...entries);
}
function handleError(err, seq) {
  if (seq === epoch) message(err.message);
}
async function listPage(target, path, render, seq, offset = 0) {
  const limit = 30;
  const sep = path.includes("?") ? "&" : "?";
  const rows = await api(
    path + sep + "offset=" + offset + "&limit=" + (limit + 1),
  );
  if (seq !== epoch) return;
  const root = $(target);
  if (!offset) root.replaceChildren();
  root.querySelector(".next-page")?.remove();
  root.append(...rows.slice(0, limit).map(render));
  if (!root.childElementCount)
    root.append(
      empty(
        target === "documents"
          ? "ไม่พบเอกสารตามเงื่อนไขที่ค้น"
          : "ไม่มีรายการในสถานะนี้",
      ),
    );
  if (rows.length > limit) {
    const more = button(
      "ดูเพิ่มเติม",
      async () => {
        more.disabled = true;
        try {
          await listPage(target, path, render, seq, offset + limit);
        } catch (err) {
          handleError(err, seq);
          more.disabled = false;
        }
      },
      "outline next-page",
    );
    root.append(more);
  }
}
function ticket(r) {
  const box = el("article", undefined, "ticket"),
    top = el("div", undefined, "ticket-head");
  top.append(
    el("span", stateName[r.state], "status " + r.state),
    el("span", date(r.created), "meta"),
  );
  box.append(top, el("h3", r.question), el("small", "เลขที่ " + r.id, "meta"));
  if (r.owner) box.append(el("p", "ผู้รับผิดชอบ · " + idName(r.owner), "meta"));
  const history = el("details", undefined, "source");
  history.append(el("summary", "ประวัติการดำเนินงาน"));
  history.ontoggle = async () => {
    if (!history.open || history.childElementCount > 1) return;
    const line = el("ul", undefined, "timeline");
    history.append(line);
    try {
      const events = await api("/api/tickets/" + r.id + "/activity");
      const actions = {
        opened: "เปิดเรื่อง",
        claimed: "รับงาน",
        released: "คืนงานเข้าคิว",
        resolved: "ปิดเรื่อง",
      };
      line.append(
        ...events.map((event) =>
          el(
            "li",
            actions[event.action] +
              " · " +
              idName(event.actor) +
              " · " +
              date(event.ts),
          ),
        ),
      );
    } catch (err) {
      line.remove();
      message(err.message);
    }
  };
  box.append(history);
  if (r.resolution) box.append(el("p", r.resolution, "notice"));
  const canReview =
    me &&
    ((me.role === "reviewer" && r.scope === "staff") ||
      (me.role === "executive" && r.scope === "executive"));
  if (canReview && r.state === "open") {
    const b = button("รับผิดชอบเคสนี้", async () => {
      b.disabled = true;
      try {
        await api("/api/tickets/" + r.id + "/claim", {});
        await show("queue");
      } catch (err) {
        message(err.message);
        b.disabled = false;
      }
    });
    box.append(b);
  }
  if (canReview && r.state === "in_progress" && r.owner === me.subject) {
    const f = el("form"),
      label = el("label", "ผลตรวจสอบ"),
      text = el("textarea");
    text.required = true;
    text.minLength = 3;
    text.maxLength = 2000;
    text.rows = 3;
    text.id = "resolution-" + r.id;
    label.htmlFor = text.id;
    const save = el("button", "บันทึกและปิดรายการ", "primary");
    f.append(label, text, save);
    const release = button("คืนงานเข้าคิว", async () => {
      release.disabled = true;
      try {
        await api("/api/tickets/" + r.id + "/release", {});
        await show("queue");
      } catch (err) {
        message(err.message);
        release.disabled = false;
      }
    });
    box.append(f, release);
    f.onsubmit = async (e) => {
      e.preventDefault();
      save.disabled = true;
      try {
        await api("/api/tickets/" + r.id + "/resolve", {
          resolution: text.value,
        });
        await show("queue");
      } catch (err) {
        message(err.message);
        save.disabled = false;
      }
    };
  }
  return box;
}
async function show(name) {
  view = views.includes(name)
    ? name
    : location.pathname === "/admin"
      ? "admin"
      : "home";
  const seq = ++epoch;
  $("admin-link").hidden = me?.role !== "admin";
  $("chat-actions").hidden = !me || view !== "ask";
  $("greeting").textContent = me
    ? "สวัสดี " + idName(me.subject)
    : "ผู้ช่วย AI";
  $("current-view").textContent = document
    .querySelector('[data-view="' + view + '"]')
    .textContent.trim();
  message("");
  for (const id of views) $(id).hidden = id !== view;
  for (const b of document.querySelectorAll("[data-view]")) {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
  try {
    if (view === "admin") {
      await showAdmin(seq);
      return;
    }
    if (view === "home") {
      await home(seq);
      return;
    }
    if (view === "ask") {
      await refreshChats(seq);
      return;
    }
    const target = view === "library" ? "documents" : "tickets";
    if (!me) {
      $(target).replaceChildren(
        gated("เข้าสู่ระบบเพื่อดูข้อมูลตามสิทธิ์ของบัญชี"),
      );
      return;
    }
    $(target).replaceChildren(empty("กำลังโหลด…"));
    if (view === "library") {
      const p = new URLSearchParams();
      if ($("doc-q").value.trim()) p.set("q", $("doc-q").value.trim());
      if ($("doc-kind").value) p.set("kind", $("doc-kind").value);
      await listPage(target, "/api/sources?" + p, docRow, seq);
    } else {
      $("queue-description").textContent = ["employee", "admin"].includes(
        me.role,
      )
        ? "ติดตามการรับเรื่องและคำตอบของคุณ"
        : "ตรวจรายการ รับผิดชอบเคส และบันทึกผลการตรวจสอบ";
      await listPage(
        target,
        "/api/tickets" +
          ($("case-state").value ? "?state=" + $("case-state").value : ""),
        ticket,
        seq,
      );
    }
  } catch (err) {
    handleError(err, seq);
  }
}
const compactMenu = window.matchMedia("(max-width: 860px)");
function setMenu(open, focusTarget) {
  const mobile = compactMenu.matches;
  open = mobile && open;
  $("sidebar").inert = mobile && !open;
  $("workspace").inert = open;
  $("menu-backdrop").hidden = !open;
  document.body.classList.toggle("menu-open", open);
  $("menu-toggle").setAttribute("aria-expanded", String(open));
  if (open) $("close-menu").focus();
  else if (focusTarget) focusTarget.focus({ preventScroll: true });
}
$("menu-toggle").onclick = () => setMenu(true);
$("close-menu").onclick = $("menu-backdrop").onclick = () =>
  setMenu(false, $("menu-toggle"));
$("sidebar").addEventListener("click", (e) => {
  if (compactMenu.matches && e.target.closest("a")) setMenu(false, $("main"));
});
document.addEventListener("keydown", (e) => {
  if (!$("workspace").inert) return;
  if (e.key === "Escape") {
    e.preventDefault();
    setMenu(false, $("menu-toggle"));
    return;
  }
  if (e.key !== "Tab") return;
  const items = Array.from(
    $("sidebar").querySelectorAll("a[href],button:not(:disabled)"),
  ).filter((n) => n.getClientRects().length);
  const first = items[0],
    last = items.at(-1);
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});
compactMenu.addEventListener("change", () =>
  setMenu(
    false,
    compactMenu.matches && $("sidebar").contains(document.activeElement)
      ? $("menu-toggle")
      : null,
  ),
);
setMenu(false);
document.querySelector(".skip").onclick = (e) => {
  e.preventDefault();
  $("main").focus();
};
function go(name) {
  if (location.hash === "#" + name) show(name);
  else location.hash = name;
}
window.addEventListener("hashchange", () => {
  if ($("workspace").inert) setMenu(false, $("main"));
  show(location.hash.slice(1));
  window.scrollTo(0, 0);
});
for (const b of document.querySelectorAll("[data-topic]"))
  b.onclick = () => {
    const v = b.dataset.topic;
    $("doc-q").value = v === "chat" ? "" : v;
    $("doc-kind").value = v === "chat" ? "chat" : "policy";
    go("library");
  };
$("library-filter").onsubmit = (e) => {
  e.preventDefault();
  show("library");
};
$("doc-kind").onchange = () => show("library");
$("case-state").onchange = $("refresh").onclick = () => show("queue");
function source(s) {
  const details = el("details", undefined, "source");
  details.append(
    el("summary", s.doc_id + " · " + s.title),
    el("p", meta(s), "meta"),
    el("p", s.body, "answer"),
  );
  if (s.original || s.pdf) details.append(pdfLink(s.doc_id));
  return details;
}
function pdfLink(docId) {
  const link = el("a", "ดาวน์โหลดไฟล์ต้นฉบับ", "text-link");
  link.href = "/api/documents/" + encodeURIComponent(docId) + "/original";
  return link;
}
async function boot() {
  const fragment = location.hash.slice(1),
    params = new URLSearchParams(fragment),
    invite = params.get("invite");
  const authFailure = new URLSearchParams(location.search).get("login");
  view = views.includes(fragment)
    ? fragment
    : invite && params.get("view") === "ask"
      ? "ask"
    : location.pathname === "/admin"
      ? "admin"
      : "home";
  if (invite || authFailure || fragment.startsWith("key="))
    history.replaceState(null, "", location.pathname);
  try {
    auth = await api("/api/auth/options", undefined, false);
    if (invite) {
      await signed(await api("/api/auth/invite", { code: invite }, false));
      return;
    }
    try {
      me = await api("/api/me", undefined, false);
    } catch (err) {
      if (err.status !== 401) throw err;
    }
    if (me) $("identity").textContent = idName(me.subject);
    await show(view);
    if (authFailure || fragment.startsWith("key=")) {
      account();
      $("login-error").textContent = authFailure
        ? "ไม่สามารถยืนยันบัญชีองค์กร กรุณาตรวจสิทธิ์หรือเข้าสู่ระบบใหม่"
        : "กรุณาใช้รหัสเชิญส่วนบุคคลหรือบัญชีองค์กร";
    }
  } catch (err) {
    await show(view);
    account();
    $("login-error").textContent = err.message;
  }
}
