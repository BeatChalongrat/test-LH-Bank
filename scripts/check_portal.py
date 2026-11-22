"""Exercise the real UI against the isolated capacity project's DB and API."""

import argparse
import re
import subprocess
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:18770"
COMPOSE = [
    "docker",
    "compose",
    "-p",
    "folio-capacity",
    "-f",
    "compose.yaml",
    "-f",
    "evaluation/capacity.compose.yaml",
]


def login(page, name, role):
    result = subprocess.run(
        COMPOSE
        + [
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "seed",
            "python",
            "-m",
            "knowledge",
            "issue-invite",
            name,
            role,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    code = result.stdout.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
        raise RuntimeError("Invitation issuance failed")
    page.goto(BASE)
    page.locator("#account").click()
    page.locator("#invite").fill(code)
    page.locator("#invite-form button").click()
    expect(page.locator("#identity")).to_have_text(name)
    page.reload()
    expect(page.locator("#identity")).to_have_text(name)


def managed_pdf(browser, admin, employee, out):
    unique = uuid4().hex[:8]
    title = f"Satellite parking expense reimbursement {unique}"
    question = (
        "How much is the satellite parking expense reimbursement allowance per month?"
    )
    pdf_page = browser.new_page()
    pdf_page.set_content(
        "<h1>Satellite parking expense reimbursement</h1>"
        "<p>Satellite parking expense reimbursement allowance is 740 baht per month.</p>"
        "<p>Employees submit the parking expense reimbursement in the Finance Portal.</p>"
    )
    pdf = pdf_page.pdf(format="A4")
    pdf_page.close()
    form = admin.locator("#pdf-upload")
    form.locator("#pdf-file").set_input_files(
        {"name": "satellite-parking.pdf", "mimeType": "application/pdf", "buffer": pdf}
    )
    form.locator("#pdf-title").fill(title)
    form.locator("#pdf-owner").fill("Finance")
    form.locator('input[value="executive"]').check()
    form.get_by_role("button", name="อัปโหลดและจัดทำดัชนี").click()
    row = admin.locator(".managed-doc").filter(has_text=title)
    expect(row).to_contain_text("พร้อมใช้ในแชต", timeout=45000)
    admin.screenshot(path=out / "admin.png", full_page=True)

    def sources(page):
        return page.request.get(BASE + "/api/sources", params={"q": title}).json()

    assert employee.request.get(BASE + "/api/admin/documents").status == 403
    assert sources(employee) == []
    executive = browser.new_page()
    login(executive, "portal.board", "executive")
    doc_id = sources(executive)[0]["doc_id"]
    download = BASE + f"/api/documents/{doc_id}/pdf"
    assert executive.request.get(download).body() == pdf
    assert employee.request.get(download).status == 404
    assert admin.request.get(download).status == 404

    def query(page, prompt):
        page.locator("[data-view=ask]").click()
        if page.locator("#new-chat").is_visible():
            page.locator("#new-chat").click()
        page.locator("#question").fill(prompt)
        page.locator("#submitq").click()
        expect(page.locator("#result .user-message").last).to_contain_text(
            prompt, timeout=35000
        )
        return page.locator("#result .agent-message").last

    expect(query(executive, question)).to_contain_text("740 baht")
    expect(executive.locator("#result .source summary").last).to_contain_text(doc_id)
    old_chat = executive.locator("#chat-history [aria-current]").get_attribute(
        "data-id"
    )
    assert "740 baht" not in query(employee, question).inner_text()
    assert doc_id not in employee.locator("#result").inner_text()

    row.get_by_role("button", name="แก้ไขสิทธิ์").click()
    edit = admin.locator("#document-access")
    edit.locator('input[value="executive"]').uncheck()
    edit.locator('input[value="employee"]').check()
    edit.get_by_role("button", name="บันทึกสิทธิ์").click()
    expect(admin.locator("#access-dialog")).to_be_hidden()
    expect(row.locator(".document-readers")).to_have_text("พนักงาน")
    assert sources(executive) == []
    assert executive.request.get(BASE + f"/api/conversations/{old_chat}").status == 404
    assert executive.request.get(download).status == 404
    executive.reload()
    expect(executive.locator("#result")).to_be_empty()
    revoked_answer = query(executive, question)
    assert "740 baht" not in revoked_answer.inner_text()
    assert doc_id not in executive.locator("#result").inner_text()
    assert sources(employee)[0]["doc_id"] == doc_id
    assert employee.request.get(download).body() == pdf
    expect(query(employee, question)).to_contain_text("740 baht")

    row.get_by_role("button", name="แก้ไขสิทธิ์").click()
    edit.locator("#pdf-enabled").uncheck()
    edit.get_by_role("button", name="บันทึกสิทธิ์").click()
    expect(row).to_contain_text("ปิดการเผยแพร่")
    assert sources(employee) == []
    assert employee.request.get(download).status == 404
    executive.close()


def department_readers(browser, admin, employee, out):
    suffix = uuid4().hex[:8].upper()
    code = "HR-" + suffix
    name = "ฝ่าย HR " + suffix
    title = "Staff orchid shuttle expense reimbursement " + suffix
    prompt = "How much is the staff orchid shuttle expense reimbursement allowance per month?"
    peer = browser.new_page()
    login(peer, "portal.peer", "employee")
    admin.locator("#open-departments").click()
    admin.locator("#department-code").fill(code)
    admin.locator("#department-name").fill(name)
    admin.get_by_role("button", name="เพิ่มแผนก", exact=True).click()
    expect(admin.locator("#membership-department")).to_have_value(code)
    admin.locator("#membership-query").fill("portal.staff")
    admin.locator("#membership-find").click()
    admin.get_by_role("button", name="เพิ่มเข้าแผนก").click()
    expect(
        admin.locator("#memberships").get_by_text("local/portal.staff")
    ).to_be_visible()
    expect(
        admin.locator("#memberships").get_by_role("button", name="นำออก")
    ).to_be_visible()
    admin.locator("#departments-dialog .close").click()

    pdf_page = browser.new_page()
    pdf_page.set_content(
        "<h1>Staff orchid shuttle expense reimbursement</h1>"
        "<p>The staff orchid shuttle expense reimbursement allowance is 380 baht per month.</p>"
    )
    pdf = pdf_page.pdf(format="A4")
    pdf_page.close()
    form = admin.locator("#pdf-upload")
    form.locator("#pdf-file").set_input_files(
        {"name": "orchid-shuttle.pdf", "mimeType": "application/pdf", "buffer": pdf}
    )
    form.locator("#pdf-title").fill(title)
    form.locator("#pdf-owner").fill("HR")
    form.locator(f'input[name="reader-department"][value="{code}"]').check()
    form.get_by_role("button", name="อัปโหลดและจัดทำดัชนี").click()
    row = admin.locator(".managed-doc").filter(has_text=title)
    expect(row).to_contain_text("พร้อมใช้ในแชต", timeout=45000)

    def ask_again(page):
        page.locator("[data-view=ask]").click()
        expect(page.locator("#new-chat")).to_be_visible()
        page.locator("#new-chat").click()
        page.locator("#question").fill(prompt)
        page.locator("#submitq").click()
        expect(page.locator("#result .agent-message")).to_be_visible(timeout=35000)
        return page.locator("#result .agent-message").inner_text()

    def sources(page):
        return page.request.get(BASE + "/api/sources", params={"q": title}).json()

    doc_id = sources(employee)[0]["doc_id"]
    url = BASE + f"/api/documents/{doc_id}/original"
    assert employee.request.get(url).body() == pdf
    assert "380 baht" in ask_again(employee)
    assert "380 baht" in ask_again(employee)
    assert sources(peer) == [] and peer.request.get(url).status == 404
    assert "380 baht" not in ask_again(peer)
    assert sources(admin) == [] and admin.request.get(url).status == 404
    admin.evaluate("window.scrollTo(0,0)")
    admin.screenshot(path=out / "admin.png")

    admin.locator("#open-departments").click()
    admin.locator("#membership-department").select_option(code)
    admin.locator("#memberships").get_by_role("button", name="นำออก").click()
    expect(admin.locator("#department-status")).to_have_text("บันทึกสมาชิกแผนกแล้ว")
    admin.locator("#departments-dialog .close").click()
    assert sources(employee) == [] and employee.request.get(url).status == 404
    assert "380 baht" not in ask_again(employee)

    row.get_by_role("button", name="แก้ไขสิทธิ์").click()
    edit = admin.locator("#document-access")
    edit.locator(f'input[name="reader-department"][value="{code}"]').uncheck()
    edit.get_by_role("searchbox").fill("portal.peer")
    edit.get_by_role("button", name="ค้นหา", exact=True).click()
    edit.get_by_role("button", name="local/portal.peer", exact=True).click()
    edit.get_by_role("button", name="บันทึกสิทธิ์").click()
    expect(row.locator(".document-readers")).to_have_text("local/portal.peer")
    assert sources(peer)[0]["doc_id"] == doc_id
    assert peer.request.get(url).body() == pdf
    assert "380 baht" in ask_again(peer)
    assert sources(employee) == []
    row.get_by_role("button", name="แก้ไขสิทธิ์").click()
    edit.locator("#pdf-enabled").uncheck()
    edit.get_by_role("button", name="บันทึกสิทธิ์").click()
    expect(row).to_contain_text("ปิดการเผยแพร่")
    peer.close()


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        desktop = browser.new_context(viewport={"width": 1360, "height": 900})
        employee = desktop.new_page()
        errors = []
        employee.on("pageerror", lambda err: errors.append(str(err)))
        login(employee, "portal.staff", "employee")
        expect(employee.locator("#home-docs .doc-row").first).to_be_visible()
        employee.evaluate("document.fonts.ready")
        employee.screenshot(path=out / "desktop.png")
        expect(employee.locator("#question")).to_be_hidden()
        employee.locator("#sidebar [data-view=ask]").click()
        expect(employee.locator("#chat-intro")).to_be_visible()
        expect(employee.locator("#home")).to_be_hidden()
        expect(employee.locator("#sidebar [data-view=ask]")).to_have_attribute(
            "aria-current", "page"
        )
        employee.screenshot(path=out / "assistant.png")
        employee.locator("#question").fill("ค่าแท็กซี่เบิกได้สูงสุดกี่บาทต่อเที่ยว")
        employee.locator("#submitq").click()
        expect(employee.locator("#result")).to_contain_text("600 บาท", timeout=35000)
        employee.locator("#result .source summary").click()
        expect(employee.locator("#result .source")).to_contain_text("FIN-01")
        employee.locator("#question").fill("แล้วต้องยื่นภายในกี่วัน")
        employee.locator("#submitq").click()
        follow = employee.locator('.chat-turn[data-context-used="true"]')
        expect(follow).to_contain_text("15 วันปฏิทิน", timeout=35000)
        expect(follow.locator(".source")).to_contain_text("FIN-01")
        thread = employee.locator("#chat-history [aria-current]").get_attribute(
            "data-id"
        )
        employee.locator("#new-chat").click()
        expect(employee.locator(".chat-turn")).to_have_count(0)
        employee.locator(f'#chat-history [data-id="{thread}"]').click()
        expect(employee.locator(".chat-turn")).to_have_count(2)
        employee.reload()
        expect(employee.locator(".chat-turn")).to_have_count(2)
        expect(employee.locator("#admin-link")).to_be_hidden()
        assert employee.request.get(BASE + "/api/admin/agent").status == 403
        admin = browser.new_context().new_page()
        admin.on("pageerror", lambda err: errors.append(str(err)))
        login(admin, "portal.operations", "admin")
        admin.locator("#admin-link").click()
        expect(admin.locator("#admin-content")).to_contain_text("knowledge-local")
        admin.screenshot(path=out / "admin.png", full_page=True)

        def query_cases():
            employee.locator("#question").fill("ดูเคสของฉัน")
            employee.locator("#question").press("Enter")
            return employee.locator(".chat-turn").last.locator(".agent-message")

        admin.get_by_role("button", name="ปิดเครื่องมือตรวจเคส", exact=True).click()
        try:
            expect(
                admin.get_by_role("button", name="เปิดเครื่องมือตรวจเคส", exact=True)
            ).to_be_visible()
            expect(query_cases()).to_contain_text("ขณะนี้ปิดเครื่องมือ")
            employee.locator("#question").fill("ลาพักร้อนต้องยื่นล่วงหน้ากี่วัน")
            employee.locator("#submitq").click()
            expect(employee.locator(".chat-turn").last).to_contain_text(
                "3 วันทำงาน", timeout=35000
            )
        finally:
            admin.get_by_role("button", name="เปิดเครื่องมือตรวจเคส", exact=True).click()
        expect(
            admin.get_by_role("button", name="ปิดเครื่องมือตรวจเคส", exact=True)
        ).to_be_visible()
        expect(query_cases()).to_contain_text("ฐานงานสนับสนุน / เคสของฉัน")
        employee.screenshot(path=out / "chat.png")
        employee.locator("[data-view=library]").click()
        employee.locator("#doc-q").fill("EX-01")
        employee.locator("#library-filter button").click()
        expect(employee.locator("#documents")).to_contain_text("ไม่พบเอกสาร")
        employee.locator("#doc-q").fill("FIN-01")
        employee.locator("#library-filter button").click()
        employee.locator("#documents .doc-row").first.click()
        expect(employee.locator("#doc-detail")).to_contain_text("600 บาท")
        employee.locator("#doc-dialog .close").click()
        employee.locator("[data-view=ask]").click()
        question = "บริษัทมีสวัสดิการค่าเล่าเรียนบุตรเท่าไร " + uuid4().hex[:8]
        employee.locator("#question").fill(question)
        employee.locator("#submitq").click()
        expect(employee.locator("#result")).to_contain_text(
            "ส่งต่อให้เจ้าหน้าที่แล้ว", timeout=35000
        )
        employee.get_by_role("button", name="ติดตามรายการ").click()
        own = employee.locator(".ticket").filter(has_text=question)
        expect(own).to_be_visible()
        reviewer = browser.new_context().new_page()
        login(reviewer, "portal.support", "reviewer")
        reviewer.locator("[data-view=queue]").click()
        item = reviewer.locator(".ticket").filter(has_text=question)
        item.get_by_role("button", name="รับผิดชอบเคสนี้").click()
        item.locator("textarea").fill("ฝ่ายบุคคลได้รับเรื่องและตรวจสอบกับเจ้าของนโยบายแล้ว")
        item.get_by_role("button", name="บันทึกและปิดรายการ").click()
        expect(item.locator(".status")).to_have_text("ปิดรายการแล้ว")
        employee.locator("#refresh").click()
        expect(own).to_contain_text("ตรวจสอบกับเจ้าของนโยบายแล้ว")
        own.locator("summary").click()
        expect(own.locator(".timeline")).to_contain_text("portal.support")
        employee.locator("[data-view=ask]").click()
        expect(employee).to_have_url(f"{BASE}/#ask")
        expect(employee.locator("#ask")).to_be_visible()
        employee.set_viewport_size({"width": 390, "height": 844})
        employee.reload()
        expect(employee.locator("#result")).to_contain_text(question)
        expect(employee.locator("#sidebar")).to_be_hidden()
        employee.locator("#menu-toggle").click()
        expect(employee.locator("#sidebar")).to_be_visible()
        employee.screenshot(path=out / "mobile-menu.png")
        employee.keyboard.press("Escape")
        expect(employee.locator("#menu-toggle")).to_be_focused()
        employee.locator("#menu-toggle").click()
        employee.locator("#sidebar [data-view=library]").click()
        expect(employee.locator("#library")).to_be_visible()
        expect(employee.locator("#sidebar")).to_be_hidden()
        employee.locator("#menu-toggle").click()
        employee.locator("#sidebar [data-view=ask]").click()
        expect(employee.locator("#result")).to_contain_text(question)
        assert employee.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        employee.locator("#menu-toggle").click()
        employee.locator("#new-chat").click()
        expect(employee.locator("#question")).to_be_focused()
        employee.screenshot(path=out / "mobile.png", full_page=True)
        employee.set_viewport_size({"width": 1360, "height": 900})
        managed_pdf(browser, admin, employee, out)
        department_readers(browser, admin, employee, out)
        employee.locator("#account").click()
        employee.locator("#logout").click()
        expect(employee.locator("#identity")).to_have_text("เข้าสู่ระบบ")
        assert employee.request.get(BASE + "/api/me").status == 401
        assert not errors, errors
        browser.close()
    print(
        "PASS: desktop sidebar, mobile drawer, chat history navigation, personal session, persistent login, sourced multi-turn chat, case tool, admin controls, library ACL, fallback ticket, assignment, resolution, history, mobile layout, PDF upload, role/department/individual document access, revocation and logout."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "var/portal-ui")
    run(parser.parse_args().out)
