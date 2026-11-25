let providerState, editingProfile;
const providerEditor = $("provider-editor");

function fillVendors() {
  const select = $("provider-vendor");
  select.replaceChildren();
  for (const vendor of providerState.providers.filter((item) =>
    item.purposes.includes($("provider-purpose").value),
  )) {
    select.add(new Option(vendor.name, vendor.id));
  }
  if ([...select.options].some((option) => option.value === "openai"))
    select.value = "openai";
}

function editProvider(profile) {
  editingProfile = profile;
  $("provider-form").reset();
  $("provider-title").textContent = profile
    ? "เปลี่ยนคีย์ / Replace API key"
    : "เพิ่มการเชื่อมต่อ / Add connection";
  $("provider-form-error").textContent = "";
  $("provider-purpose").value = profile?.purpose || "chat";
  fillVendors();
  for (const id of [
    "provider-name",
    "provider-purpose",
    "provider-vendor",
    "provider-model",
  ])
    $(id).disabled = Boolean(profile);
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
    try {
      await run();
    } catch (error) {
      $("provider-result").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
  return button;
}

async function loadProviders(seq) {
  const data = await api("/api/admin/providers");
  if (seq !== epoch || me?.role !== "admin") return;
  providerState = data;
  $("step-connect").disabled = false;
  const list = $("provider-rows");
  list.replaceChildren();
  $("provider-caption").textContent =
    data.embedding_mode === "managed" && !data.indexed
      ? "ตั้งค่าการเชื่อมต่อสองงาน เลือก “ค้นเนื้อหาในเอกสาร” เพื่อเตรียมคลัง และ “เขียนและตอบคำถาม” สำหรับแชต / Set up document search and chat, then test each connection."
      : "ทดสอบการเชื่อมต่อแล้วกดเลือกใช้ / Test a connection, then select it for use.";
  const activeName = (purpose) => {
    const id = data.routes.find(
      (route) => route.purpose === purpose,
    )?.profile_id;
    return data.profiles.find((profile) => profile.id === id)?.name;
  };
  const summary = el("p", undefined, "connection-summary");
  const search =
    activeName("embedding") ||
    (data.embedding_mode === "managed"
      ? "รอเลือก / Choose connection"
      : "ค่าเริ่มต้น / System default");
  const chat =
    activeName("chat") ||
    (data.chat_configured
      ? "ค่าเริ่มต้น / System default"
      : "รอเลือก / Choose connection");
  summary.append(
    el("span", "ค้นเอกสาร / Search · " + search),
    el("span", "ตอบแชต / Chat · " + chat),
  );
  list.append(summary);
  if (!data.profiles.length)
    list.append(
      el("p", "ยังไม่มีการเชื่อมต่อที่บันทึก / No saved connections", "muted"),
    );
  for (const profile of data.profiles) {
    const route = data.routes.find((item) => item.purpose === profile.purpose);
    const active = route.profile_id === profile.id;
    const row = el("div", undefined, "provider-row");
    const info = el("div");
    const vendor =
      data.providers.find((v) => v.id === profile.provider)?.name ||
      profile.provider;
    info.append(
      el("strong", profile.name),
      el(
        "p",
        [
          profile.purpose === "chat" ? "ตอบแชต / Chat" : "ค้นเอกสาร / Search",
          vendor,
          profile.model,
        ].join(" · "),
        "muted",
      ),
    );
    if (profile.purpose === "embedding")
      info.append(el("small", "ID · " + profile.id, "muted"));
    const actions = el("div", undefined, "provider-actions");
    if (active)
      actions.append(el("span", "กำลังใช้งาน / Active", "provider-active"));
    actions.append(
      providerAction("ทดสอบ / Test", async () => {
        const result = await api(
          "/api/admin/providers/" + profile.id + "/test",
          {},
        );
        $("provider-result").textContent =
          profile.name +
          " เชื่อมต่อสำเร็จ / Connected · " +
          result.latency_ms +
          " ms";
      }),
    );
    if (
      !active &&
      (profile.purpose === "chat" || data.embedding_mode === "managed")
    ) {
      actions.append(
        providerAction("เลือกใช้ / Activate", async () => {
          if (
            profile.purpose === "embedding" &&
            !window.confirm(
              "เริ่มสร้างดัชนีด้วย " +
                profile.name +
                " หรือไม่ / Send document text, including restricted documents, for indexing to " +
                vendor,
            )
          )
            return;
          await api("/api/admin/provider-routes/" + profile.purpose, {
            profile_id: profile.id,
            version: route.version,
          });
          await show("admin");
        }),
      );
    }
    actions.append(
      providerAction("เปลี่ยนคีย์ / Replace key", () => editProvider(profile)),
    );
    if (!active)
      actions.append(
        providerAction("ลบ / Delete", async () => {
          if (
            !window.confirm(
              "ลบโปรไฟล์ " +
                profile.name +
                " และคีย์ที่บันทึกไว้หรือไม่ / Delete this connection and its key?",
            )
          )
            return;
          await api("/api/admin/providers/" + profile.id + "/remove", {
            version: profile.version,
          });
          await loadProviders(epoch);
        }),
      );
    if (active && profile.purpose === "chat")
      actions.append(
        providerAction(
          "ใช้ค่าเริ่มต้นของระบบ / Use system default",
          async () => {
            await api("/api/admin/provider-routes/chat", {
              profile_id: null,
              version: route.version,
            });
            await show("admin");
          },
        ),
      );
    row.append(info, actions);
    list.append(row);
  }
}

$("provider-purpose").onchange = fillVendors;
$("step-connect").onclick = () => editProvider(null);
$("provider-close").onclick = () => providerEditor.close();
providerEditor.addEventListener("close", () => {
  $("provider-key").value = "";
});
$("provider-form").onsubmit = async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  try {
    const api_key = $("provider-key").value;
    if (editingProfile) {
      await api("/api/admin/providers/" + editingProfile.id + "/key", {
        api_key,
        version: editingProfile.version,
      });
    } else {
      await api("/api/admin/providers", {
        name: $("provider-name").value,
        model: $("provider-model").value,
        provider: $("provider-vendor").value,
        purpose: $("provider-purpose").value,
        api_key,
      });
    }
    providerEditor.close();
    await loadProviders(epoch);
    $("provider-result").textContent =
      "บันทึกแล้ว กดทดสอบการเชื่อมต่อ / Saved. Test your connection.";
  } catch (error) {
    $("provider-form-error").textContent = error.message;
  } finally {
    submit.disabled = false;
  }
};
