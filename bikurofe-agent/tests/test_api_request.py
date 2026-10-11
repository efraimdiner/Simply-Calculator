"""Runs the agent with the real Anthropic SDK against a local stand-in API to check the request shape."""

import asyncio

import anthropic
from fastapi import FastAPI, Request

from agent.agent import ShiftAgent
from agent.browser import BrowserController
from agent.hub import Hub
from conftest import free_port, login, run, serve_in_thread


def message(*content, stop_reason="tool_use"):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": list(content), "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_request_shape_and_history_round_trip(portal, settings):
    requests = []
    replies = [
        message({"type": "thinking", "thinking": "בודק את הדף", "signature": "sig-1"},
                {"type": "tool_use", "id": "toolu_1", "name": "get_page_state", "input": {}}),
        message({"type": "tool_use", "id": "toolu_2", "name": "finish", "input": {"summary": "הכול תקין", "success": True}}),
    ]
    api = FastAPI()

    @api.post("/v1/messages")
    async def messages(request: Request):
        requests.append({"headers": dict(request.headers), "body": await request.json()})
        return replies[len(requests) - 1]

    port = free_port()
    server = serve_in_thread(api, port)

    async def scenario():
        hub = Hub("ask_all")
        browser = BrowserController(settings)
        await browser.start()
        try:
            await login(browser, portal.url)
            await browser.open(settings.start_url)
            client = anthropic.AsyncAnthropic(api_key="test-key", base_url=f"http://127.0.0.1:{port}", max_retries=0)
            agent = ShiftAgent(settings, hub, browser, client=client)
            agent.start("בדיקה")
            await asyncio.wait_for(agent.wait(), 30)
        finally:
            await browser.close()
        return hub

    try:
        hub = run(scenario())
    finally:
        server.should_exit = True

    assert [e["kind"] for e in hub.entries][-1] == "done", hub.entries
    assert len(requests) == 2
    first = requests[0]
    betas = first["headers"]["anthropic-beta"]
    assert "context-management-2025-06-27" in betas and "server-side-fallback-2026-07-01" in betas
    body = first["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "medium"}
    assert body["context_management"]["edits"][0]["type"] == "clear_tool_uses_20250919"
    assert {t["name"] for t in body["tools"]} >= {"click", "type_text", "ask_user", "finish"}
    first_content = body["messages"][0]["content"]
    assert first_content[1]["text"].startswith("URL: ")
    assert first_content[2]["type"] == "image"

    second = requests[1]["body"]["messages"]
    # The assistant turn goes back unchanged, thinking signature included (append-only history).
    assert second[1]["content"][0] == {"type": "thinking", "thinking": "בודק את הדף", "signature": "sig-1"}
    result = second[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert [b["type"] for b in result["content"]] == ["text", "image"]
    # Earlier messages are byte-identical between requests.
    assert requests[1]["body"]["messages"][0] == body["messages"][0]
