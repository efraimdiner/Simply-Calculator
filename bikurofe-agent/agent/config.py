from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

# ask_all: every browser action waits for approval.
# ask_changes: navigation runs freely; clicks, typing and dialogs wait for approval.
# autonomous: nothing waits (still logged, still stoppable).
APPROVAL_MODES = ("ask_all", "ask_changes", "autonomous")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass
class Settings:
    start_url: str = ""
    allowed_domains: list[str] = field(default_factory=list)
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_steps: int = 80
    approval_mode: str = "ask_all"
    headless: bool = False
    browser_channel: str = ""
    executable_path: str = ""
    profile_dir: Path = ROOT / "browser-profile"
    viewport_width: int = 1280
    viewport_height: int = 900
    host: str = "127.0.0.1"
    port: int = 8000
    logs_dir: Path = ROOT / "logs"
    playbooks_dir: Path = ROOT / "playbooks"
    notes_file: Path = ROOT / "portal_notes.md"

    def is_allowed_url(self, url: str) -> bool:
        if url.startswith(("about:", "data:", "chrome-error:")):
            return True
        host = (urlparse(url).hostname or "").lower()
        if not self.allowed_domains:
            return False
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / "config.toml"
    if not path.exists():
        raise SystemExit(
            f"Missing config file: {path}\n"
            "Copy config.example.toml to config.toml and fill in the portal address."
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    portal = data.get("portal", {})
    agent = data.get("agent", {})
    browser = data.get("browser", {})
    server = data.get("server", {})

    s = Settings()
    s.start_url = portal.get("start_url", "")
    s.allowed_domains = [d.lower().strip() for d in portal.get("allowed_domains", [])]
    if not s.allowed_domains and s.start_url:
        s.allowed_domains = [(urlparse(s.start_url).hostname or "").lower()]
    s.model = agent.get("model", s.model)
    s.effort = agent.get("effort", s.effort)
    s.max_steps = int(agent.get("max_steps", s.max_steps))
    s.approval_mode = agent.get("approval_mode", s.approval_mode)
    s.headless = bool(browser.get("headless", s.headless))
    s.browser_channel = browser.get("channel", s.browser_channel)
    s.executable_path = browser.get("executable_path", s.executable_path)
    if "profile_dir" in browser:
        s.profile_dir = ROOT / browser["profile_dir"]
    s.viewport_width = int(browser.get("viewport_width", s.viewport_width))
    s.viewport_height = int(browser.get("viewport_height", s.viewport_height))
    s.host = server.get("host", s.host)
    s.port = int(server.get("port", s.port))

    if s.approval_mode not in APPROVAL_MODES:
        raise SystemExit(f"approval_mode must be one of {APPROVAL_MODES}")
    if s.effort not in EFFORT_LEVELS:
        raise SystemExit(f"effort must be one of {EFFORT_LEVELS}")
    if not s.start_url:
        raise SystemExit("portal.start_url is required in config.toml")
    return s
