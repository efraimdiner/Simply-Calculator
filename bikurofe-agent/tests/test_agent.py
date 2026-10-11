import asyncio

from agent.agent import ShiftAgent, needs_approval
from agent.browser import BrowserController
from agent.hub import Decision, Hub
from conftest import (FakeClient, element_id, latest_page_text, latest_tool_result, login, respond_to_requests,
                      response, run, tool_call, wait_for_request)


async def start(settings, portal, mode, script):
    hub = Hub(mode, settings.logs_dir)
    browser = BrowserController(settings)
    await browser.start()
    await login(browser, portal.url)
    await browser.open(settings.start_url)
    client = FakeClient(script)
    agent = ShiftAgent(settings, hub, browser, client=client)
    return hub, browser, client, agent


def test_approval_modes():
    assert needs_approval("ask_all", "click")
    assert needs_approval("ask_all", "navigate")
    assert not needs_approval("ask_all", "scroll")
    assert not needs_approval("ask_all", "get_page_state")
    assert needs_approval("ask_changes", "type_text")
    assert not needs_approval("ask_changes", "navigate")
    assert not needs_approval("autonomous", "click")


def test_full_assignment_with_approvals(portal, settings):
    """Every action waits for the user; edits and rejections reach the model; nothing runs without approval."""
    seen = {}

    script = [
        lambda kw: response(tool_call("click", element_id=element_id(latest_page_text(kw), r'"פתח קריאה".*1042'),
                                      reason="פתיחת קריאה 1042")),
        lambda kw: response(tool_call("select_option", element_id=element_id(latest_page_text(kw), r'name="doctor"'),
                                      option="ד״ר מיכל לוי", reason="שיבוץ ד״ר לוי")),
        lambda kw: response(tool_call("type_text", element_id=element_id(latest_page_text(kw), r'name="notes"'),
                                      text="להגיע עד 18:00", clear_first=True, press_enter=False, reason="הערה")),
        lambda kw: (seen.setdefault("typed_result", latest_tool_result(kw)),
                    response(tool_call("click", element_id=element_id(latest_page_text(kw), r'"שמור שיבוץ"'),
                                       reason="שמירה")))[1],
        lambda kw: (seen.setdefault("dialog_state", latest_page_text(kw)),
                    response(tool_call("handle_dialog", accept=True, text="", reason="אישור השמירה")))[1],
        lambda kw: response(tool_call("click", element_id=element_id(latest_page_text(kw), r'"פתח קריאה".*1041'),
                                      reason="פתיחת קריאה 1041")),
        lambda kw: (seen.setdefault("rejected_result", latest_tool_result(kw)),
                    response(tool_call("finish", summary="שובץ רופא לקריאה 1042", success=True)))[1],
    ]

    async def scenario():
        hub, browser, client, agent = await start(settings, portal, "ask_all", script)
        try:
            agent.start("שבץ רופא לקריאה 1042")
            await respond_to_requests(hub, [
                Decision(True),
                Decision(True),
                Decision(True, edited_text="להגיע עד 19:00"),
                Decision(True),
                Decision(True),
                Decision(False, comment="לא לגעת בקריאה 1041"),
            ])
            await asyncio.wait_for(agent.wait(), 30)
        finally:
            await browser.close()
        return hub, client

    hub, client = run(scenario())

    visit = portal.app.state.visits[1]
    assert visit["doctor"] == "ד״ר מיכל לוי"
    assert visit["notes"] == "להגיע עד 19:00"
    assert "19:00" in seen["typed_result"] and "edited" in seen["typed_result"]
    assert "confirm" in seen["dialog_state"] and "1042" in seen["dialog_state"]
    assert "REJECTED" in seen["rejected_result"] and "לא לגעת בקריאה 1041" in seen["rejected_result"]
    assert portal.app.state.visits[0]["doctor"] == ""

    kinds = [e["kind"] for e in hub.entries]
    assert kinds.count("approved") == 5 and kinds.count("rejected") == 1 and kinds[-1] == "done"
    assert hub.status == "idle"

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert call["fallbacks"] == "default"
    assert all(t["strict"] for t in call["tools"])
    assert (settings.logs_dir).exists()


def test_autonomous_mode_runs_without_asking(portal, settings):
    script = [
        lambda kw: response(tool_call("navigate", url=settings.start_url.replace("/visits", "/visits/1044"),
                                      reason="פתיחת קריאה")),
        lambda kw: response(tool_call("select_option", element_id=element_id(latest_page_text(kw), r'name="doctor"'),
                                      option="ד״ר דני ביטון", reason="שיבוץ")),
        lambda kw: response(tool_call("finish", summary="בוצע", success=True)),
    ]

    async def scenario():
        hub, browser, client, agent = await start(settings, portal, "autonomous", script)
        try:
            agent.start("בדיקה")
            await asyncio.wait_for(agent.wait(), 30)
            assert "1044" in browser.current_url()
        finally:
            await browser.close()
        return hub

    hub = run(scenario())
    kinds = [e["kind"] for e in hub.entries]
    assert "proposal" not in kinds and kinds.count("action") == 2


def test_page_change_during_approval_cancels_action(portal, settings):
    """If the row under the approved button changes while the user decides, the click must not happen."""
    seen = {}
    script = [
        lambda kw: response(tool_call("click", element_id=element_id(latest_page_text(kw), r'"פתח קריאה".*1042'),
                                      reason="פתיחת קריאה 1042")),
        lambda kw: (seen.setdefault("result", latest_tool_result(kw)),
                    response(tool_call("finish", summary="עצרתי", success=False)))[1],
    ]

    async def scenario():
        hub, browser, client, agent = await start(settings, portal, "ask_all", script)
        try:
            agent.start("בדיקה")
            await wait_for_request(hub)
            # Simulate the portal re-rendering the table: same button, different patient row.
            await browser.current_page().evaluate(
                "() => { const a = document.querySelector('a[href=\"/visits/1042\"]');"
                " a.closest('tr').cells[1].innerText = 'מטופל אחר'; }")
            hub.resolve(hub.pending.id, Decision(True))
            await asyncio.wait_for(agent.wait(), 30)
            url = browser.current_url()
        finally:
            await browser.close()
        return url

    url = run(scenario())
    assert url.endswith("/visits")
    assert "NOT performed" in seen["result"]


def test_question_and_stop(portal, settings):
    seen = {}
    script = [
        lambda kw: response(tool_call("ask_user", question="לאיזה רופא לשבץ?")),
        lambda kw: (seen.setdefault("answer", latest_tool_result(kw)),
                    response(tool_call("click", element_id=element_id(latest_page_text(kw), r'"פתח קריאה".*1041'),
                                       reason="פתיחה")))[1],
    ]

    async def scenario():
        hub, browser, client, agent = await start(settings, portal, "ask_all", script)
        try:
            agent.start("בדיקה")
            await respond_to_requests(hub, ["לד״ר כהן"])
            await wait_for_request(hub)
            await agent.stop()
            url = browser.current_url()
        finally:
            await browser.close()
        return hub, url

    hub, url = run(scenario())
    assert "לד״ר כהן" in seen["answer"]
    assert url.endswith("/visits")
    assert hub.entries[-1]["kind"] == "stopped" and hub.status == "idle" and hub.pending is None
