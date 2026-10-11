from __future__ import annotations

import asyncio
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import Settings  # noqa: E402
from mock_portal.app import create_app as create_portal  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_in_thread(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    return server


@pytest.fixture
def portal():
    app = create_portal()
    port = free_port()
    server = serve_in_thread(app, port)
    yield SimpleNamespace(app=app, url=f"http://127.0.0.1:{port}")
    server.should_exit = True


@pytest.fixture
def settings(portal, tmp_path) -> Settings:
    return Settings(
        start_url=portal.url + "/visits",
        allowed_domains=["127.0.0.1"],
        headless=True,
        profile_dir=tmp_path / "profile",
        logs_dir=tmp_path / "logs",
        notes_file=tmp_path / "portal_notes.md",
        playbooks_dir=tmp_path / "playbooks",
        max_steps=20,
        executable_path=os.environ.get("TEST_CHROMIUM_PATH", ""),
    )


def run(coro, timeout: float = 60):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def login(browser, portal_url: str) -> None:
    await browser.context.add_cookies([{"name": "session", "value": "ok", "url": portal_url}])


def element_id(state_text: str, pattern: str) -> int:
    """Find the id of the first element line in a page-state text matching a regex."""
    for line in state_text.splitlines():
        if re.search(pattern, line):
            m = re.match(r"\[(\d+)\]", line)
            if m:
                return int(m.group(1))
    raise AssertionError(f"no element matching {pattern!r} in:\n{state_text}")


# ---------- scripted stand-in for the Anthropic client ----------

def tool_call(name: str, **args):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}_{time.monotonic_ns()}", name=name, input=args)


def response(*blocks, stop_reason="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


def latest_page_text(kwargs) -> str:
    """Text of the most recent page state sent to the model."""
    for message in reversed(kwargs["messages"]):
        if message["role"] != "user":
            continue
        for block in reversed(message["content"]):
            texts = []
            if block.get("type") == "text":
                texts = [block["text"]]
            elif block.get("type") == "tool_result" and isinstance(block["content"], list):
                texts = [b["text"] for b in block["content"] if b.get("type") == "text"]
            for text in texts:
                if text.startswith("URL: "):
                    return text
    raise AssertionError("no page state in the conversation")


def latest_tool_result(kwargs) -> str:
    last = kwargs["messages"][-1]["content"]
    for block in last:
        if block.get("type") == "tool_result":
            content = block["content"]
            return content if isinstance(content, str) else content[0]["text"]
    return ""


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        return step(kwargs) if callable(step) else step


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)


async def wait_for_request(hub, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while hub.pending is None:
        if time.monotonic() > deadline:
            raise AssertionError("agent never asked: " + repr(hub.entries[-3:]))
        await asyncio.sleep(0.02)
    return hub.pending


async def respond_to_requests(hub, replies, timeout: float = 30):
    """Answer the agent's approval requests / questions in order, like the user on the dashboard."""
    for reply in replies:
        req = await wait_for_request(hub, timeout)
        assert hub.resolve(req.id, reply)
        while hub.pending is req:
            await asyncio.sleep(0.02)
