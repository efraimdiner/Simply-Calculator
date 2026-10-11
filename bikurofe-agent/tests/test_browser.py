from agent.browser import ActionError, BrowserController
from conftest import element_id, login, run


def test_snapshot_lists_elements_with_row_context(portal, settings):
    async def scenario():
        b = BrowserController(settings)
        await b.start()
        try:
            await login(b, portal.url)
            await b.open(portal.url + "/visits")
            state = await b.snapshot()
            text = state.to_text()
            assert "קריאות פתוחות" in text
            assert state.screenshot and state.screenshot[:2] == b"\xff\xd8"
            open_1042 = element_id(text, r'"פתח קריאה".*1042')
            assert b.element(open_1042)["href"] == "/visits/1042"
        finally:
            await b.close()

    run(scenario())


def test_confirm_dialog_blocks_until_handled(portal, settings):
    async def scenario():
        b = BrowserController(settings)
        await b.start()
        try:
            await login(b, portal.url)
            await b.open(portal.url + "/visits/1044")
            text = (await b.snapshot()).to_text()
            await b.select_option(element_id(text, r"name=\"doctor\""), "ד״ר רונית פרץ")
            await b.click(element_id(text, r'"שמור שיבוץ"'))

            state = await b.snapshot()
            assert state.dialog and state.dialog["type"] == "confirm"
            assert "1044" in state.to_text()
            assert portal.app.state.visits[3]["doctor"] == ""

            await b.handle_dialog(accept=True)
            state = await b.snapshot()
            assert state.dialog is None
            assert "קריאה 1044 נשמרה" in state.text
            assert portal.app.state.visits[3]["doctor"] == "ד״ר רונית פרץ"
        finally:
            await b.close()

    run(scenario())


def test_dismissed_dialog_saves_nothing(portal, settings):
    async def scenario():
        b = BrowserController(settings)
        await b.start()
        try:
            await login(b, portal.url)
            await b.open(portal.url + "/visits/1041")
            text = (await b.snapshot()).to_text()
            await b.select_option(element_id(text, r"name=\"doctor\""), "ד״ר אבי כהן")
            await b.click(element_id(text, r'"שמור שיבוץ"'))
            await b.handle_dialog(accept=False)
            assert portal.app.state.visits[0]["doctor"] == ""
            assert "/visits/1041" in b.current_url()
        finally:
            await b.close()

    run(scenario())


def test_refuses_password_fields_and_foreign_domains(portal, settings):
    async def scenario():
        b = BrowserController(settings)
        await b.start()
        try:
            await b.open(portal.url + "/login")
            text = (await b.snapshot()).to_text()
            pwd = element_id(text, r"input\[password\]")
            try:
                await b.type_text(pwd, "secret", True, False)
                raise AssertionError("typed into a password field")
            except ActionError as e:
                assert "password" in str(e)
            try:
                await b.navigate("https://example.com/")
                raise AssertionError("navigated outside the portal")
            except ActionError as e:
                assert "outside" in str(e)
        finally:
            await b.close()

    run(scenario())


def test_unknown_option_is_rejected(portal, settings):
    async def scenario():
        b = BrowserController(settings)
        await b.start()
        try:
            await login(b, portal.url)
            await b.open(portal.url + "/visits/1041")
            text = (await b.snapshot()).to_text()
            try:
                await b.select_option(element_id(text, r"name=\"doctor\""), "ד״ר לא קיים")
                raise AssertionError("selected a missing option")
            except ActionError as e:
                assert "ד״ר מיכל לוי" in str(e)
        finally:
            await b.close()

    run(scenario())
