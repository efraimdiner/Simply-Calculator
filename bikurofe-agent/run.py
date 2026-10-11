"""Start the shift agent: opens the portal browser and the local dashboard."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from pathlib import Path

import uvicorn

from agent.agent import ShiftAgent
from agent.browser import ActionError, BrowserController
from agent.config import ROOT, load_settings
from agent.hub import Hub
from agent.server import create_app


async def main(config_path: Path, open_dashboard: bool) -> None:
    settings = load_settings(config_path)
    hub = Hub(settings.approval_mode, settings.logs_dir)
    browser = BrowserController(settings)
    await browser.start()
    try:
        try:
            await browser.open(settings.start_url)
        except ActionError as e:
            hub.log("error", str(e))
        hub.set_screenshot(await browser.preview_page())

        agent = ShiftAgent(settings, hub, browser)
        app = create_app(settings, hub, browser, agent)
        server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning"))

        url = f"http://{settings.host}:{settings.port}"
        print(f"Dashboard: {url}")
        print("Sign in to the portal in the browser window that opened, then start a task from the dashboard.")
        print("Press Ctrl+C to quit.")
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print("WARNING: ANTHROPIC_API_KEY is not set - tasks will fail until it is.")
        if open_dashboard:
            webbrowser.open(url)
        await server.serve()
        await agent.stop()
    finally:
        await browser.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--no-open", action="store_true", help="don't open the dashboard in the default browser")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.config, not args.no_open))
    except KeyboardInterrupt:
        pass
