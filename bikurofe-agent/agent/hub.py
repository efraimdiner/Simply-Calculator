"""Shared state between the agent and the dashboard: activity log, pending approvals and questions."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_ENTRIES = 500


@dataclass
class Decision:
    approved: bool
    comment: str = ""
    edited_text: str | None = None


@dataclass
class PendingRequest:
    id: str
    kind: str  # "approval" | "question"
    title: str
    reason: str = ""
    details: list[tuple[str, str]] = field(default_factory=list)
    editable_text: str | None = None
    image: bytes | None = None
    created_at: float = field(default_factory=time.time)
    future: asyncio.Future | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "reason": self.reason,
            "details": [{"label": k, "value": v} for k, v in self.details],
            "editable_text": self.editable_text,
            "has_image": self.image is not None,
            "created_at": self.created_at,
        }


class Hub:
    def __init__(self, mode: str, logs_dir: Path | None = None):
        self.mode = mode
        self.status = "idle"  # idle | running | waiting_approval | waiting_answer
        self.task_text = ""
        self.entries: list[dict] = []
        self.version = 0
        self.pending: PendingRequest | None = None
        self.notes: list[str] = []
        self.screenshot: bytes | None = None
        self.screenshot_version = 0
        self._next_entry = 1
        self._logs_dir = logs_dir

    def bump(self) -> None:
        self.version += 1

    def log(self, kind: str, text: str, detail: str = "") -> None:
        entry = {"id": self._next_entry, "ts": time.time(), "kind": kind, "text": text, "detail": detail}
        self._next_entry += 1
        self.entries.append(entry)
        del self.entries[:-MAX_ENTRIES]
        self._audit({"event": "log", **entry})
        self.bump()

    def set_status(self, status: str) -> None:
        self.status = status
        self.bump()

    def set_screenshot(self, image: bytes | None) -> None:
        if image:
            self.screenshot = image
            self.screenshot_version += 1
            self.bump()

    def add_note(self, text: str) -> None:
        self.notes.append(text)
        self.log("note", text)

    def drain_notes(self) -> list[str]:
        notes, self.notes = self.notes, []
        return notes

    async def request_approval(
        self,
        title: str,
        reason: str,
        details: list[tuple[str, str]],
        image: bytes | None,
        editable_text: str | None = None,
    ) -> Decision:
        req = PendingRequest(
            id=uuid.uuid4().hex[:12],
            kind="approval",
            title=title,
            reason=reason,
            details=details,
            editable_text=editable_text,
            image=image,
        )
        self._audit({"event": "approval_requested", "id": req.id, "title": title, "reason": reason,
                     "details": details, "text": editable_text})
        decision = await self._wait(req, "waiting_approval")
        self._audit({"event": "approval_decided", "id": req.id, "approved": decision.approved,
                     "comment": decision.comment, "edited_text": decision.edited_text})
        return decision

    async def ask(self, question: str) -> str:
        req = PendingRequest(id=uuid.uuid4().hex[:12], kind="question", title=question)
        self._audit({"event": "question", "id": req.id, "question": question})
        answer = await self._wait(req, "waiting_answer")
        self._audit({"event": "answer", "id": req.id, "answer": answer})
        return answer

    async def _wait(self, req: PendingRequest, status: str):
        req.future = asyncio.get_running_loop().create_future()
        self.pending = req
        previous = self.status
        self.set_status(status)
        try:
            return await req.future
        finally:
            self.pending = None
            if self.status == status:
                self.status = previous
            self.bump()

    def resolve(self, req_id: str, result) -> bool:
        req = self.pending
        if not req or req.id != req_id or not req.future or req.future.done():
            return False
        req.future.set_result(result)
        return True

    def cancel_pending(self) -> None:
        req = self.pending
        if req and req.future and not req.future.done():
            req.future.cancel()

    def state(self, since_entry: int = 0) -> dict:
        return {
            "version": self.version,
            "status": self.status,
            "mode": self.mode,
            "task": self.task_text,
            "pending": self.pending.public() if self.pending else None,
            "entries": [e for e in self.entries if e["id"] > since_entry],
            "screenshot_version": self.screenshot_version,
        }

    def _audit(self, record: dict) -> None:
        if not self._logs_dir:
            return
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        path = self._logs_dir / f"audit-{datetime.now():%Y-%m-%d}.jsonl"
        record = {"time": datetime.now().isoformat(timespec="seconds"), **record}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
