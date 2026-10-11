"""Playwright wrapper: reads the portal page for the model and performs single actions."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext, Dialog, Frame, Locator, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from .config import Settings

MAX_ELEMENTS = 300
MAX_TEXT_CHARS = 15000
ACTION_TIMEOUT_S = 30

# Tags every visible interactive element with data-agent-id and returns a description of each.
EXTRACT_JS = r"""
([startId, maxItems, maxText]) => {
  const SEL = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=checkbox],' +
    '[role=radio], [role=tab], [role=menuitem], [role=option], [role=combobox], [role=switch], [onclick],' +
    '[contenteditable="true"], [contenteditable=""]';
  const clean = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  for (const el of document.querySelectorAll('[data-agent-id]')) el.removeAttribute('data-agent-id');
  const vw = window.innerWidth, vh = window.innerHeight;
  const found = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (el.tagName === 'INPUT' && el.type === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0) continue;
    const inView = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
    found.push({el, inView});
  }
  let keep = found;
  if (found.length > maxItems) {
    const inView = found.filter(f => f.inView);
    const rest = found.filter(f => !f.inView).slice(0, Math.max(0, maxItems - inView.length));
    const chosen = new Set([...inView.slice(0, maxItems), ...rest]);
    keep = found.filter(f => chosen.has(f));
  }
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText, 60);
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria, 60);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const ref = document.getElementById(by);
      if (ref) return clean(ref.innerText, 60);
    }
    return clean(el.getAttribute('title'), 60);
  };
  const items = [];
  let id = startId;
  for (const {el, inView} of keep) {
    el.setAttribute('data-agent-id', String(id));
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const isPassword = tag === 'input' && type === 'password';
    const isField = tag === 'input' || tag === 'textarea';
    let text = isField ? '' : clean(el.innerText, 100);
    if (tag === 'input' && (type === 'submit' || type === 'button')) text = clean(el.value, 100);
    let value = '';
    if (isField && !['checkbox', 'radio', 'submit', 'button'].includes(type)) {
      value = isPassword ? (el.value ? '********' : '') : clean(el.value, tag === 'textarea' ? 300 : 100);
    } else if (el.isContentEditable) {
      value = clean(el.innerText, 300);
    }
    const rowEl = el.closest('tr, [role=row], li');
    items.push({
      id, tag, type,
      role: el.getAttribute('role') || '',
      text,
      label: labelOf(el),
      name: el.getAttribute('name') || '',
      placeholder: clean(el.getAttribute('placeholder'), 60),
      value,
      href: tag === 'a' ? clean(el.getAttribute('href'), 120) : '',
      checked: (type === 'checkbox' || type === 'radio') ? el.checked
        : (el.getAttribute('aria-checked') ? el.getAttribute('aria-checked') === 'true' : null),
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      options: tag === 'select'
        ? Array.from(el.options).slice(0, 60).map(o => ({text: clean(o.text, 60), value: o.value, selected: o.selected}))
        : [],
      row: rowEl && rowEl !== el ? clean(rowEl.innerText, 120) : '',
      in_view: inView,
      password: isPassword,
    });
    id++;
  }
  const body = document.body ? document.body.innerText : '';
  const text = body.replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim().slice(0, maxText);
  return {elements: items, text, truncated: body.length > maxText};
}
"""

# Identity of an element, compared before and after approval so a re-rendered page can't redirect a click.
FINGERPRINT_JS = r"""
(el) => {
  const clean = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const rowEl = el.closest('tr, [role=row], li, form');
  return {
    tag: el.tagName.toLowerCase(),
    text: clean(el.innerText || el.value || '', 100),
    name: el.getAttribute('name') || '',
    label: el.labels && el.labels.length ? clean(el.labels[0].innerText, 60) : clean(el.getAttribute('aria-label'), 60),
    row: rowEl && rowEl !== el ? clean(rowEl.innerText, 200) : '',
    password: el.tagName === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'password',
  };
}
"""

POINT_FINGERPRINT_JS = r"""
([x, y]) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  const clean = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  return {tag: el.tagName.toLowerCase(), text: clean(el.innerText || el.value || '', 100)};
}
"""

HIGHLIGHT_JS = r"""
(b) => {
  const d = document.createElement('div');
  d.id = '__agent_highlight__';
  Object.assign(d.style, {
    position: 'fixed', left: (b.x - 4) + 'px', top: (b.y - 4) + 'px',
    width: (b.width + 8) + 'px', height: (b.height + 8) + 'px',
    border: '3px solid #e11d48', borderRadius: b.round ? '50%' : '6px',
    boxShadow: '0 0 0 9999px rgba(0,0,0,0.25)', zIndex: '2147483647', pointerEvents: 'none',
  });
  document.documentElement.appendChild(d);
}
"""

UNHIGHLIGHT_JS = "() => document.getElementById('__agent_highlight__')?.remove()"


class ActionError(Exception):
    """An action could not be performed; the message is returned to the model."""


@dataclass
class PageState:
    url: str
    title: str = ""
    elements: list[dict] = field(default_factory=list)
    text: str = ""
    text_truncated: bool = False
    screenshot: bytes | None = None
    dialog: dict | None = None
    tabs: list[str] = field(default_factory=list)
    allowed: bool = True

    def to_text(self) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}"]
        if not self.allowed:
            lines.append("WARNING: this page is outside the allowed portal domains. Go back or ask the user.")
        if len(self.tabs) > 1:
            lines.append(f"Open tabs ({len(self.tabs)}), you are on the last one: " + " | ".join(self.tabs))
        if self.dialog:
            lines.append(
                f"A browser {self.dialog['type']} dialog is open and blocks the page. "
                f"Message: {json.dumps(self.dialog['message'], ensure_ascii=False)}. "
                "Handle it with handle_dialog before anything else."
            )
            return "\n".join(lines)
        lines.append("")
        lines.append("Interactive elements (use these ids; they change on every page state):")
        lines.extend(describe_element(e) for e in self.elements)
        if not self.elements:
            lines.append("(none)")
        lines.append("")
        lines.append("Page text:" + (" (truncated)" if self.text_truncated else ""))
        lines.append(self.text or "(empty)")
        return "\n".join(lines)


def describe_element(e: dict) -> str:
    q = lambda s: json.dumps(s, ensure_ascii=False)  # noqa: E731
    kind = e["tag"]
    if e["role"]:
        kind += f"[role={e['role']}]"
    elif e["type"] and e["tag"] in ("input", "button"):
        kind += f"[{e['type']}]"
    parts = [f"[{e['id']}]", kind]
    if e["text"]:
        parts.append(q(e["text"]))
    if e["label"] and e["label"] != e["text"]:
        parts.append(f"label={q(e['label'])}")
    if e["name"]:
        parts.append(f"name={q(e['name'])}")
    if e["placeholder"]:
        parts.append(f"placeholder={q(e['placeholder'])}")
    if e["value"]:
        parts.append(f"value={q(e['value'])}")
    if e["checked"] is not None:
        parts.append("checked" if e["checked"] else "unchecked")
    if e["options"]:
        opts = ", ".join(q(o["text"]) + ("*" if o["selected"] else "") for o in e["options"])
        parts.append(f"options=[{opts}] (* = selected)")
    if e["href"]:
        parts.append(f"href={q(e['href'])}")
    if e["disabled"]:
        parts.append("disabled")
    if e["row"] and e["row"] != e["text"]:
        parts.append(f"row={q(e['row'])}")
    if not e["in_view"]:
        parts.append("(offscreen)")
    return " ".join(parts)


def element_title(e: dict) -> str:
    """Short Hebrew-friendly name for an element, used in approval cards."""
    name = e.get("text") or e.get("label") or e.get("placeholder") or e.get("name") or e.get("tag", "")
    return name[:60]


class BrowserController:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.pending_dialog: Dialog | None = None
        self.lock = asyncio.Lock()
        self._pw = None
        self._dialog_event = asyncio.Event()
        self._blocked_action: asyncio.Future | None = None
        self._frames: dict[int, Frame] = {}
        self._elements: dict[int, dict] = {}

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        extra = {}
        if self.settings.executable_path:
            extra["executable_path"] = self.settings.executable_path
        elif self.settings.browser_channel:
            extra["channel"] = self.settings.browser_channel
        self.context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.profile_dir),
            headless=self.settings.headless,
            **extra,
            viewport={"width": self.settings.viewport_width, "height": self.settings.viewport_height},
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
        )
        self.context.on("page", self._on_new_page)
        for p in self.context.pages:
            self._attach(p)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self._pw:
            await self._pw.stop()

    def _attach(self, page: Page) -> None:
        page.on("dialog", self._on_dialog)

    def _on_new_page(self, page: Page) -> None:
        self._attach(page)
        self.page = page

    def _on_dialog(self, dialog: Dialog) -> None:
        self.pending_dialog = dialog
        self._dialog_event.set()

    def current_page(self) -> Page:
        if self.page and not self.page.is_closed():
            return self.page
        open_pages = [p for p in self.context.pages if not p.is_closed()]
        if not open_pages:
            raise ActionError("No open browser tab.")
        self.page = open_pages[-1]
        return self.page

    def current_url(self) -> str:
        try:
            return self.current_page().url
        except ActionError:
            return ""

    def dialog_info(self) -> dict | None:
        d = self.pending_dialog
        if not d:
            return None
        return {"type": d.type, "message": d.message, "default_value": d.default_value}

    # ---------- reading ----------

    async def snapshot(self, screenshot: bool = True) -> PageState:
        async with self.lock:
            page = self.current_page()
            tabs = [p.url for p in self.context.pages if not p.is_closed()]
            state = PageState(url=page.url, tabs=tabs, allowed=self.settings.is_allowed_url(page.url))
            if self.pending_dialog:
                state.dialog = self.dialog_info()
                return state
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except PlaywrightError:
                pass
            self._frames.clear()
            self._elements.clear()
            texts = []
            next_id = 1
            for frame in page.frames:
                if frame.is_detached():
                    continue
                try:
                    res = await frame.evaluate(EXTRACT_JS, [next_id, MAX_ELEMENTS, MAX_TEXT_CHARS])
                except PlaywrightError:
                    continue
                for el in res["elements"]:
                    self._frames[el["id"]] = frame
                    self._elements[el["id"]] = el
                    next_id = el["id"] + 1
                state.elements.extend(res["elements"])
                if res["text"]:
                    header = "" if frame == page.main_frame else f"[frame {frame.url}]\n"
                    texts.append(header + res["text"])
                state.text_truncated = state.text_truncated or res["truncated"]
            state.text = "\n\n".join(texts)
            try:
                state.title = await page.title()
            except PlaywrightError:
                pass
            if screenshot:
                state.screenshot = await self._screenshot(page)
            return state

    async def _screenshot(self, page: Page) -> bytes | None:
        try:
            return await page.screenshot(type="jpeg", quality=60, timeout=10000)
        except PlaywrightError:
            return None

    def element(self, element_id: int) -> dict:
        el = self._elements.get(element_id)
        if not el:
            raise ActionError(f"Element {element_id} is not in the latest page state. Call get_page_state and use a current id.")
        return el

    async def _locate(self, element_id: int) -> Locator:
        frame = self._frames.get(element_id)
        if not frame or frame.is_detached():
            raise ActionError(f"Element {element_id} is not in the latest page state. Call get_page_state and use a current id.")
        loc = frame.locator(f'[data-agent-id="{element_id}"]')
        if await loc.count() == 0:
            raise ActionError(f"Element {element_id} is no longer on the page (the page changed). Call get_page_state.")
        return loc.first

    async def fingerprint(self, element_id: int) -> dict:
        loc = await self._locate(element_id)
        try:
            return await loc.evaluate(FINGERPRINT_JS, timeout=5000)
        except PlaywrightError as e:
            raise ActionError(f"Could not read element {element_id}: {e.message}") from e

    async def point_fingerprint(self, x: int, y: int) -> dict | None:
        return await self.current_page().evaluate(POINT_FINGERPRINT_JS, [x, y])

    # ---------- approval previews ----------

    async def preview_element(self, element_id: int) -> bytes | None:
        async with self.lock:
            page = self.current_page()
            loc = await self._locate(element_id)
            try:
                await loc.scroll_into_view_if_needed(timeout=3000)
                box = await loc.bounding_box(timeout=3000)
            except PlaywrightError:
                box = None
            return await self._highlighted_screenshot(page, box)

    async def preview_point(self, x: int, y: int) -> bytes | None:
        async with self.lock:
            page = self.current_page()
            return await self._highlighted_screenshot(page, {"x": x - 14, "y": y - 14, "width": 28, "height": 28, "round": True})

    async def preview_page(self) -> bytes | None:
        if self.pending_dialog:
            return None
        async with self.lock:
            return await self._screenshot(self.current_page())

    async def _highlighted_screenshot(self, page: Page, box: dict | None) -> bytes | None:
        if not box:
            return await self._screenshot(page)
        try:
            await page.evaluate(HIGHLIGHT_JS, box)
            return await self._screenshot(page)
        finally:
            try:
                await page.evaluate(UNHIGHLIGHT_JS)
            except PlaywrightError:
                pass

    # ---------- actions ----------

    async def _run(self, action) -> None:
        """Run a Playwright action; return early if it opens a dialog (the action then waits for handle_dialog)."""
        if self.pending_dialog:
            raise ActionError("A browser dialog is open. Handle it with handle_dialog first.")
        self._dialog_event.clear()
        task = asyncio.ensure_future(action())
        dialog_wait = asyncio.ensure_future(self._dialog_event.wait())
        done, _ = await asyncio.wait({task, dialog_wait}, timeout=ACTION_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            dialog_wait.cancel()
            try:
                task.result()
            except PlaywrightError as e:
                raise ActionError(f"The action failed: {e.message.splitlines()[0]}") from e
            await self._settle()
            return
        if dialog_wait in done:
            self._blocked_action = task
            return
        task.cancel()
        dialog_wait.cancel()
        raise ActionError("The action timed out.")

    async def _settle(self) -> None:
        if self.pending_dialog:
            return
        try:
            await self.current_page().wait_for_load_state("domcontentloaded", timeout=8000)
        except PlaywrightError:
            pass
        await asyncio.sleep(0.6)

    async def click(self, element_id: int) -> None:
        loc = await self._locate(element_id)
        await self._run(lambda: loc.click(timeout=10000))

    async def click_at(self, x: int, y: int) -> None:
        page = self.current_page()
        await self._run(lambda: page.mouse.click(x, y))

    async def type_text(self, element_id: int, text: str, clear_first: bool, press_enter: bool) -> None:
        if (await self.fingerprint(element_id))["password"]:
            raise ActionError("Typing into password fields is not allowed. Ask the user to sign in themselves.")
        loc = await self._locate(element_id)

        async def do_type():
            if clear_first:
                await loc.fill(text, timeout=10000)
            else:
                await loc.click(timeout=10000)
                await loc.press("End")
                await loc.press_sequentially(text)
            if press_enter:
                await loc.press("Enter")

        await self._run(do_type)

    async def select_option(self, element_id: int, option: str) -> None:
        loc = await self._locate(element_id)
        el = self.element(element_id)
        match = next((o for o in el["options"] if o["text"] == option or o["value"] == option), None)
        if el["options"] and not match:
            raise ActionError(f"No option {option!r} in this list. Options: " + ", ".join(o["text"] for o in el["options"]))
        value = match["value"] if match else option
        await self._run(lambda: loc.select_option(value=value, timeout=10000))

    async def press_key(self, key: str) -> None:
        page = self.current_page()
        await self._run(lambda: page.keyboard.press(key))

    async def navigate(self, url: str) -> None:
        if not self.settings.is_allowed_url(url):
            raise ActionError(f"{url} is outside the allowed portal domains ({', '.join(self.settings.allowed_domains)}).")
        page = self.current_page()
        await self._run(lambda: page.goto(url, wait_until="domcontentloaded", timeout=30000))

    async def go_back(self) -> None:
        page = self.current_page()
        await self._run(lambda: page.go_back(wait_until="domcontentloaded", timeout=30000))

    async def scroll(self, direction: str) -> None:
        page = self.current_page()
        w, h = self.settings.viewport_width, self.settings.viewport_height
        dy = int(h * 0.8) * (1 if direction == "down" else -1)

        async def do_scroll():
            await page.mouse.move(w / 2, h / 2)
            await page.mouse.wheel(0, dy)

        await self._run(do_scroll)

    async def handle_dialog(self, accept: bool, text: str = "") -> None:
        dialog = self.pending_dialog
        if not dialog:
            raise ActionError("There is no open dialog.")
        self.pending_dialog = None
        self._dialog_event.clear()
        try:
            if accept:
                await (dialog.accept(text) if dialog.type == "prompt" else dialog.accept())
            else:
                await dialog.dismiss()
        except PlaywrightError as e:
            raise ActionError(f"Could not handle the dialog: {e.message}") from e
        blocked, self._blocked_action = self._blocked_action, None
        if blocked:
            # The action that opened the dialog finishes now, or opens another dialog.
            dialog_wait = asyncio.ensure_future(self._dialog_event.wait())
            await asyncio.wait({blocked, dialog_wait}, timeout=ACTION_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED)
            dialog_wait.cancel()
            if blocked.done() and not blocked.cancelled() and blocked.exception():
                raise ActionError(f"The action after the dialog failed: {blocked.exception()}")
            if not blocked.done():
                self._blocked_action = blocked
        await self._settle()

    async def open(self, url: str) -> None:
        page = self.current_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightError as e:
            raise ActionError(f"Could not open {url}: {e.message.splitlines()[0]}") from e
