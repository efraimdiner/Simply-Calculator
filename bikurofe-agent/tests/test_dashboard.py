import asyncio
import os
import urllib.error
import urllib.request

import uvicorn
from playwright.async_api import async_playwright

from agent.agent import ShiftAgent
from agent.browser import BrowserController
from agent.hub import Hub
from agent.server import create_app
from conftest import FakeClient, element_id, free_port, latest_page_text, login, response, run, tool_call


def http(url: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None) -> int:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status
    except urllib.error.HTTPError as e:
        return e.code


def test_dashboard_approval_flow(portal, settings, tmp_path):
    settings.playbooks_dir.mkdir()
    (settings.playbooks_dir / "check.md").write_text("# בדיקת קריאות\nעבור על הקריאות.", encoding="utf-8")
    script = [
        lambda kw: response(tool_call("click", element_id=element_id(latest_page_text(kw), r'"פתח קריאה".*1042'),
                                      reason="פתיחת קריאה 1042 של שרה אברהם")),
        lambda kw: response(tool_call("finish", summary="נפתחה קריאה 1042", success=True)),
    ]
    shots = os.environ.get("DASHBOARD_SHOTS_DIR", str(tmp_path))

    async def scenario():
        hub = Hub("ask_all")
        browser = BrowserController(settings)
        await browser.start()
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        try:
            await login(browser, portal.url)
            await browser.open(settings.start_url)
            agent = ShiftAgent(settings, hub, browser, client=FakeClient(script))
            server = uvicorn.Server(uvicorn.Config(create_app(settings, hub, browser, agent),
                                                   host="127.0.0.1", port=port, log_level="warning"))
            serving = asyncio.create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.05)

            json_headers = {"Content-Type": "application/json"}
            assert await asyncio.to_thread(http, base + "/api/task", "POST", json_headers, b'{"instructions":"x"}') == 403
            assert await asyncio.to_thread(http, base + "/api/state", "GET", {"Host": "evil.example"}) == 400
            assert not agent.running

            async with async_playwright() as pw:
                viewer = await pw.chromium.launch(executable_path=settings.executable_path or None)
                page = await viewer.new_page(viewport={"width": 1400, "height": 1000})
                await page.goto(base)
                await page.select_option("#playbook", "check")
                await page.fill("#instructions", "פתח את קריאה 1042")
                await page.click("#start-btn")
                await page.wait_for_selector("#pending:not(.hidden) >> text=מבקש אישור", timeout=15000)
                card = await page.inner_text("#pending")
                assert "פתיחת קריאה 1042 של שרה אברהם" in card
                assert "שרה אברהם" in card  # row context shown to the user
                await page.screenshot(path=os.path.join(shots, "dashboard-approval.png"), full_page=True)

                await page.click("text=✓ אשר וביצוע")
                await page.wait_for_selector("#log >> text=נפתחה קריאה 1042", timeout=15000)
                await page.wait_for_selector("#status >> text=ממתין למשימה", timeout=15000)
                await page.screenshot(path=os.path.join(shots, "dashboard-done.png"), full_page=True)
                await viewer.close()

            assert browser.current_url().endswith("/visits/1042")
            assert "בדיקת קריאות" in hub.task_text and "פתח את קריאה 1042" in hub.task_text
            server.should_exit = True
            await serving
        finally:
            await browser.close()

    run(scenario())
