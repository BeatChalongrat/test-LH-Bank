"use strict";
let departmentList = [];
let identityRequest = 0;
const readerFields = new Map();

function accountLabel(account) {
  return account.label === idName(account.subject)
    ? account.subject
    : account.label + " · " + account.subject;
}

for (const container of document.querySelectorAll(".scoped-readers")) {
  const departments = el(
    "fieldset",
    undefined,
    "role-options scoped-departments",
  );
  const people = el("fieldset", undefined, "scope-people");
  const row = el("div", undefined, "identity-search");
  const query = el("input");
  query.type = "search";
  query.maxLength = 260;
  query.setAttribute("aria-label", "ค้นหาบัญชีที่จะให้สิทธิ์");
  query.placeholder = "ชื่อหรือรหัสบัญชี";
  const selected = el("div", undefined, "selected-accounts");
  const results = el("div", undefined, "identity-results");
  const entries = new Map();
  let searchRun = 0;
  function drawPeople() {
    selected.replaceChildren(
      ...Array.from(entries, ([subject, label]) => {
        const item = button(
          label + " ×",
          () => {
            entries.delete(subject);
            drawPeople();
          },
          "account-chip",
        );
        item.setAttribute("aria-label", "เอาสิทธิ์ " + label + " ออก");
        return item;
      }),
    );
  }
  async function search() {
    if (!query.value.trim()) return;
    const run = ++searchRun;
    try {
      const accounts = await api(
        "/api/admin/people?q=" + encodeURIComponent(query.value.trim()),
      );
      if (run !== searchRun) return;
      results.replaceChildren(
        ...accounts.map((account) =>
          button(
            accountLabel(account),
            () => {
              if (!entries.has(account.subject) && entries.size >= 30) return;
              entries.set(account.subject, accountLabel(account));
              drawPeople();
              results.replaceChildren();
              query.value = "";
              query.focus();
            },
            "identity-result",
          ),
        ),
      );
      if (!accounts.length) results.append(el("p", "ไม่พบบัญชี", "meta"));
    } catch (error) {
      if (run === searchRun)
        results.replaceChildren(el("p", error.message, "meta"));
    }
  }
  query.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      search();
    }
  });
  row.append(query, button("ค้นหา", search));
  people.append(el("legend", "สิทธิ์รายบุคคล"), row, selected, results);
  container.append(departments, people);
  readerFields.set(container.closest("form"), {
    departments,
    entries,
    reset(people = []) {
      searchRun++;
      query.value = "";
      results.replaceChildren();
      entries.clear();
      for (const subject of people) entries.set(subject, subject);
      drawPeople();
    },
  });
}

function renderDepartmentFields() {
  for (const field of readerFields.values()) {
    const chosen = new Set(
      Array.from(
        field.departments.querySelectorAll("input:checked"),
        (input) => input.value,
      ),
    );
    field.departments.replaceChildren(el("legend", "สิทธิ์รายแผนก"));
    for (const dept of departmentList) {
      const label = el("label"),
        check = el("input");
      check.type = "checkbox";
      check.value = dept.code;
      check.checked = chosen.has(dept.code);
      check.name = "reader-department";
      label.append(
        check,
        document.createTextNode(dept.name + " (" + dept.code + ")"),
      );
      field.departments.append(label);
    }
    if (!departmentList.length)
      field.departments.append(
        el("span", "เพิ่มแผนกได้จากเมนูจัดการแผนก", "meta"),
      );
  }
  const chooser = $("membership-department"),
    current = chooser.value;
  chooser.replaceChildren(
    new Option("เลือกแผนก", ""),
    ...departmentList.map((dept) => new Option(dept.name, dept.code)),
  );
  chooser.value = departmentList.some((dept) => dept.code === current)
    ? current
    : "";
}

async function refreshDepartments() {
  const subject = me?.subject;
  const list = await api("/api/admin/departments");
  if (me?.subject !== subject) return;
  departmentList = list;
  renderDepartmentFields();
}

function resetDirectory() {
  identityRequest++;
  departmentList = [];
  for (const field of readerFields.values()) field.reset();
  renderDepartmentFields();
  $("department-create").reset();
  $("membership-query").value = $("department-status").textContent = "";
  $("memberships").replaceChildren();
  if ($("departments-dialog").open) $("departments-dialog").close();
}

async function loadMembers() {
  const code = $("membership-department").value;
  const root = $("memberships"),
    status = $("department-status");
  const term = $("membership-query").value.trim();
  const run = ++identityRequest;
  root.replaceChildren();
  if (!code) return;
  try {
    const people = await api(
      "/api/admin/people?department=" +
        encodeURIComponent(code) +
        "&q=" +
        encodeURIComponent(term),
    );
    if (run !== identityRequest) return;
    for (const account of people) {
      const row = el("div", undefined, "member-row");
      const change = button(
        account.member ? "นำออก" : "เพิ่มเข้าแผนก",
        async () => {
          change.disabled = true;
          try {
            await api(
              "/api/admin/departments/" +
                encodeURIComponent(code) +
                "/membership",
              { subject: account.subject, member: !account.member },
            );
            status.textContent = "บันทึกสมาชิกแผนกแล้ว";
            await loadMembers();
          } catch (error) {
            status.textContent = error.message;
            change.disabled = false;
          }
        },
      );
      row.append(el("span", accountLabel(account)), change);
      root.append(row);
    }
    if (!people.length)
      root.append(
        el("p", term ? "ไม่พบบัญชี" : "ยังไม่มีสมาชิกในแผนก", "meta"),
      );
  } catch (error) {
    if (run === identityRequest) status.textContent = error.message;
  }
}

$("open-departments").onclick = () => {
  $("department-status").textContent = "";
  $("departments-dialog").showModal();
  refreshDepartments()
    .then(loadMembers)
    .catch((error) => ($("department-status").textContent = error.message));
};
$("membership-department").onchange = loadMembers;
$("membership-find").onclick = loadMembers;
$("membership-query").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadMembers();
  }
});
$("department-create").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget,
    action = form.querySelector("button");
  action.disabled = true;
  try {
    const row = await api("/api/admin/departments", {
      code: $("department-code").value.trim().toUpperCase(),
      name: $("department-name").value.trim(),
    });
    form.reset();
    await refreshDepartments();
    $("membership-department").value = row.code;
    $("department-status").textContent = "เพิ่มแผนกแล้ว";
    await loadMembers();
  } catch (error) {
    $("department-status").textContent =
      error.status === 409 ? "รหัสแผนกนี้มีอยู่แล้ว" : error.message;
  } finally {
    action.disabled = false;
  }
};
