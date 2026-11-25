"""Exercise the employee and reviewer journeys in separate browser sessions."""

import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from hr_demo import invite, local_base


def main():
    base = local_base()
    output = Path("var/support-walkthrough")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        errors = []

        def sign_in(role, view):
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/#invite=" + invite(role) + "&view=" + view)
            expect(page.locator("#identity")).to_have_text("demo." + role)
            return page

        employee = sign_in("non-hr", "ask")
        employee.locator("#new-chat").click()
        employee.evaluate("document.fonts.ready")
        employee.screenshot(path=output / "start.png")
        employee.set_viewport_size({"width": 390, "height": 844})
        expect(
            employee.get_by_role("button", name=re.compile("ใบเสร็จหาย"))
        ).to_be_visible()
        assert employee.evaluate("document.documentElement.scrollWidth <= innerWidth")
        employee.screenshot(path=output / "mobile.png", full_page=True)
        employee.set_viewport_size({"width": 1440, "height": 960})
        employee.locator('[data-sample="notes"]').click()
        employee.locator("#chat-file-chip .message-file").click()
        expect(employee.locator("#file-preview-body")).to_contain_text("TR-2186")
        employee.locator("#file-preview-close").click()
        employee.locator("#submitq").click()
        turn = employee.locator("#result article[data-tool]").last
        expect(turn.locator(".answer")).to_contain_text("10:19", timeout=45000)
        expect(turn.locator(".answer")).to_contain_text("ฝ่ายการเงินเทียบวันเดินทาง")
        employee.locator("#question").fill("ดูเคสของฉัน")
        employee.locator("#submitq").click()
        expect(employee.locator(".chat-case-list a").first).to_be_visible()

        employee.locator("#new-chat").click()
        employee.get_by_role("button", name=re.compile("ใบเสร็จหาย")).click()
        expect(employee.locator("#question")).to_have_value("บิลค่าเดินทางหาย ทำไงดี")
        employee.locator("#submitq").click()
        expect(turn.locator(".source summary")).to_contain_text("FIN-02", timeout=45000)
        expect(employee.locator(".history-entry[aria-current]")).to_have_text(
            "การเบิกเมื่อใบเสร็จสูญหาย"
        )
        employee.get_by_role("button", name="ร่างอีเมลจากคำตอบนี้").click()
        employee.locator("#submitq").click()
        expect(turn).to_have_attribute("data-tool", "policy_email")
        expect(turn.locator(".agent-message > .answer")).to_contain_text("เรียนทีมงาน")
        expect(turn.locator(".source summary")).to_contain_text("FIN-02")
        employee.locator("#question").fill("เบิกค่าแท็กซี่ต่างประเทศได้สูงสุดเท่าไร")
        employee.locator("#submitq").click()
        follow = employee.get_by_role("button", name="ติดตามเรื่องนี้")
        expect(follow).to_be_visible(timeout=45000)
        chat_url = employee.url
        follow.click()
        expect(employee.locator(".ticket .status")).to_have_text("รอรับเรื่อง")
        ticket_url = employee.url
        ticket = ticket_url.split("#queue/")[1]
        reviewer = sign_in("reviewer", "queue")
        reviewer.locator("#case-q").fill(ticket)
        reviewer.locator("#refresh").click()
        expect(reviewer.locator(".ticket")).to_have_count(1)
        reviewer.get_by_role("button", name="รับผิดชอบเคสนี้").click()
        resolution = "ตรวจ FIN-01 แล้ว เรื่องนี้ส่งให้ฝ่ายการเงินยืนยันวงเงินเดินทางต่างประเทศ"
        reviewer.locator(".ticket textarea").fill(resolution)
        reviewer.get_by_role("button", name="บันทึกและปิดรายการ").click()
        expect(reviewer.locator(".ticket .status")).to_have_text("ปิดรายการแล้ว")
        employee.reload()
        expect(employee.locator(".ticket .notice")).to_have_text(resolution)
        employee.locator(".ticket summary").click()
        expect(employee.locator(".timeline")).to_contain_text("demo.reviewer")
        employee.screenshot(path=output / "case.png")
        employee.goto(chat_url)
        expect(follow).to_be_visible()
        employee.locator("#question").fill("เรื่องนี้ได้ผลหรือยัง")
        employee.locator("#submitq").click()
        expect(turn.locator(".answer")).to_contain_text(resolution)
        employee.screenshot(path=output / "closed-in-chat.png")
        other = sign_in("hr", "ask")
        other.goto(ticket_url)
        expect(other.locator("#tickets")).to_contain_text("ไม่พบรายการที่คุณเปิดได้")
        assert not other.locator(".ticket").count()
        assert not errors, errors
        browser.close()
    print("PASS employee, reviewer, case privacy, attachment, chat and mobile layout")


if __name__ == "__main__":
    main()
