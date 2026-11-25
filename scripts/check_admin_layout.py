"""Open the real admin controls at desktop, split-window and mobile widths."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from hr_demo import invite, local_base


def main():
    output = Path("var/support-walkthrough")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(local_base() + "/admin#invite=" + invite("admin"))
        expect(page.locator("#identity")).to_have_text("demo.admin")
        expect(page.locator("#admin-updated")).to_contain_text("อัปเดตล่าสุด")
        page.evaluate("document.fonts.ready")
        for width, height in [
            (1440, 900),
            (950, 520),
            (861, 650),
            (768, 700),
            (390, 844),
            (320, 568),
        ]:
            page.set_viewport_size({"width": width, "height": height})
            expect(page.locator("#managed-list .managed-doc").first).to_be_visible()
            page.locator("#step-connect").click()
            expect(page.locator("#provider-vendor option").first).to_be_attached()
            expect(page.locator("#provider-editor")).to_be_visible()
            page.locator("#provider-close").click()
            if width == 950:
                page.screenshot(path=output / "admin-split.png")
            page.locator("#step-upload").click()
            expect(page.locator("#upload-dialog")).to_be_visible()
            assert page.evaluate("""() => {
                const within = (inner, outer) => inner.left >= outer.left - 1 && inner.right <= outer.right + 1;
                const dialog = document.querySelector('#upload-dialog');
                const box = dialog.getBoundingClientRect();
                return box.left >= 0 && box.right <= innerWidth &&
                    document.documentElement.scrollWidth <= innerWidth &&
                    dialog.scrollWidth <= dialog.clientWidth + 1 &&
                    [...dialog.querySelectorAll('fieldset,.file-picker')].every(el => within(el.getBoundingClientRect(), box));
            }"""), f"Layout overflow at {width}px"
            if width == 950:
                page.locator("#pdf-file").set_input_files(
                    {
                        "name": "hr-test.txt",
                        "mimeType": "text/plain",
                        "buffer": b"HR document",
                    }
                )
                expect(page.locator("#selected-file")).to_contain_text("hr-test.txt")
                page.locator(
                    "#pdf-upload .scoped-departments input[value=HR-DEMO]"
                ).check()
                page.screenshot(path=output / "upload-hr.png")
            page.keyboard.press("Escape")
            expect(page.locator("#upload-dialog")).to_be_hidden()
            expect(page.locator("#step-upload")).to_be_focused()
        page.set_viewport_size({"width": 1440, "height": 900})
        with page.expect_response(
            lambda r: r.url.endswith("/api/admin/agent") and r.status == 200
        ):
            page.locator("#admin-refresh").click()
        expect(page.locator("#admin-updated")).to_contain_text("อัปเดตล่าสุด")
        page.locator("#step-test").click()
        expect(page.locator("#question")).to_be_visible()
        expect(page.locator("#question")).to_have_value("")
        assert not errors, errors
        browser.close()
    print(
        "PASS admin model settings, upload and HR selection, refresh, test chat and six viewport sizes"
    )


if __name__ == "__main__":
    main()
