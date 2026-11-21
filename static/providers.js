let providerState, editingProfile;
const providerEditor = $("provider-editor");

function fillVendors() {
  const select = $("provider-vendor");
  select.replaceChildren();
  for (const vendor of providerState.providers.filter((item) => item.purposes.includes($("provider-purpose").value))) {
    select.add(new Option(vendor.name, vendor.id));
  }
}

function editProvider(profile) {
  editingProfile = profile;
  $("provider-form").reset();
  $("provider-title").textContent = profile ? "เปลี่ยน API key" : "เพิ่มโปรไฟล์โมเดล";
  $("provider-form-error").textContent = "";
  $("provider-purpose").value = profile?.purpose || "chat";
  fillVendors();
  for (const id of ["provider-name", "provider-purpose", "provider-vendor", "provider-model"]) $(id).disabled = Boolean(profile);
  if (profile) {
    $("provider-name").value = profile.name;
    $("provider-model").value = profile.model;
    $("provider-vendor").value = profile.provider;
  }
  providerEditor.showModal();
}

function providerAction(label, run) {
  const button = el("button", label, "outline");
  button.type = "button";
  button.onclick = async () => {
    button.disabled = true;
    $("provider-result").textContent = "";
    try { await run(); } catch (error) { $("provider-result").textContent = error.message; }
    finally { button.disabled = false; }
  };
  return button;
}

async function loadProviders(seq) {
  const data = await api("/api/admin/providers");
  if (seq !== epoch || me?.role !== "admin") return;
  providerState = data;
  const list = $("provider-rows");
  list.replaceChildren();
  $("provider-caption").textContent = data.embedding_mode === "managed" && !data.indexed
    ? "เลือก embeddings เพื่อเริ่มจัดทำดัชนี จากนั้นเลือกโปรไฟล์สำหรับแชต"
    : "เลือกโมเดลที่ใช้ตอบแชต และทดสอบการเชื่อมต่อด้วยข้อความตัวอย่าง";
  if (!data.profiles.length) list.append(el("p", "ยังไม่มีโปรไฟล์ที่บันทึก", "muted"));
  for (const profile of data.profiles) {
    const route = data.routes.find((item) => item.purpose === profile.purpose);
    const active = route.profile_id === profile.id;
    const row = el("div", undefined, "provider-row");
    const info = el("div");
    const vendor = data.providers.find((v) => v.id === profile.provider)?.name || profile.provider;
    info.append(el("strong", profile.name), el("p", [profile.purpose === "chat" ? "แชต" : "Embeddings", vendor, profile.model].join(" · "), "muted"));
    if (profile.purpose === "embedding") info.append(el("small", "ID · " + profile.id, "muted"));
    const actions = el("div", undefined, "provider-actions");
    if (active) actions.append(el("span", "กำลังใช้งาน", "provider-active"));
    actions.append(providerAction("ทดสอบ", async () => {
      const result = await api("/api/admin/providers/" + profile.id + "/test", {});
      $("provider-result").textContent = profile.name + " เชื่อมต่อสำเร็จ · " + result.latency_ms + " ms";
    }));
    if (!active && (profile.purpose === "chat" || data.embedding_mode === "managed")) {
      actions.append(providerAction("เลือกใช้", async () => {
        await api("/api/admin/provider-routes/" + profile.purpose, {profile_id: profile.id, version: route.version});
        await show("admin");
      }));
    }
    actions.append(providerAction("เปลี่ยนคีย์", () => editProvider(profile)));
    if (!active) actions.append(providerAction("ลบ", async () => {
      if (!window.confirm("ลบโปรไฟล์ " + profile.name + " และคีย์ที่บันทึกไว้หรือไม่")) return;
      await api("/api/admin/providers/" + profile.id + "/remove", {version:profile.version});
      await loadProviders(epoch);
    }));
    if (active && profile.purpose === "chat") actions.append(providerAction("กลับไปใช้ค่า deployment", async () => {
      await api("/api/admin/provider-routes/chat", {profile_id:null, version:route.version});
      await show("admin");
    }));
    row.append(info, actions); list.append(row);
  }
}

$("provider-purpose").onchange = fillVendors;
$("provider-add").onclick = () => editProvider(null);
$("provider-close").onclick = () => providerEditor.close();
providerEditor.addEventListener("close", () => { $("provider-key").value = ""; });
$("provider-form").onsubmit = async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  try {
    const api_key = $("provider-key").value;
    if (editingProfile) {
      await api("/api/admin/providers/" + editingProfile.id + "/key", {api_key, version: editingProfile.version});
    } else {
      await api("/api/admin/providers", {name:$("provider-name").value, model:$("provider-model").value,
        provider:$("provider-vendor").value, purpose:$("provider-purpose").value, api_key});
    }
    providerEditor.close();
    await loadProviders(epoch);
    $("provider-result").textContent = "บันทึกโปรไฟล์แล้ว";
  } catch (error) { $("provider-form-error").textContent = error.message; }
  finally { submit.disabled = false; }
};
