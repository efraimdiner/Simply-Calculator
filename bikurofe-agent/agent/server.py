"""Local dashboard (FastAPI): live view, approvals, questions, task control."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import MODE_LABELS, ShiftAgent
from .browser import ActionError, BrowserController
from .config import APPROVAL_MODES, Settings
from .hub import Decision, Hub

STATIC = Path(__file__).parent / "static"
NO_CACHE = {"Cache-Control": "no-store"}


class TaskBody(BaseModel):
    instructions: str = ""
    playbook: str = ""


class DecisionBody(BaseModel):
    id: str
    approved: bool
    comment: str = ""
    edited_text: str | None = None


class AnswerBody(BaseModel):
    id: str
    answer: str


class NoteBody(BaseModel):
    text: str


class ModeBody(BaseModel):
    mode: str


def list_playbooks(settings: Settings) -> list[dict]:
    items = []
    if settings.playbooks_dir.exists():
        for path in sorted(settings.playbooks_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            first = next((line.lstrip("# ").strip() for line in text.splitlines() if line.strip()), path.stem)
            items.append({"name": path.stem, "title": first})
    return items


def create_app(settings: Settings, hub: Hub, browser: BrowserController, agent: ShiftAgent) -> FastAPI:
    app = FastAPI(title="Bikurofe shift agent", docs_url=None, redoc_url=None)
    # Only this computer may use the dashboard, and only from the dashboard page itself:
    # the custom header blocks other websites from approving actions through the browser.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def require_dashboard_header(request: Request, call_next):
        if request.method == "POST" and request.headers.get("x-dashboard") != "1":
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers=NO_CACHE)

    @app.get("/api/state")
    async def state(since: int = 0):
        data = hub.state(since)
        data["modes"] = [{"id": m, "label": MODE_LABELS[m]} for m in APPROVAL_MODES]
        data["start_url"] = settings.start_url
        return JSONResponse(data, headers=NO_CACHE)

    @app.get("/api/screenshot")
    async def screenshot():
        if not hub.screenshot:
            return Response(status_code=204)
        return Response(hub.screenshot, media_type="image/jpeg", headers=NO_CACHE)

    @app.get("/api/pending-image")
    async def pending_image():
        if not hub.pending or not hub.pending.image:
            return Response(status_code=204)
        return Response(hub.pending.image, media_type="image/jpeg", headers=NO_CACHE)

    @app.get("/api/playbooks")
    async def playbooks():
        return list_playbooks(settings)

    @app.post("/api/task")
    async def start_task(body: TaskBody):
        if agent.running:
            raise HTTPException(409, "משימה אחרת כבר רצה")
        parts = []
        if body.playbook:
            path = settings.playbooks_dir / f"{Path(body.playbook).name}.md"
            if not path.exists():
                raise HTTPException(404, "התסריט לא נמצא")
            parts.append(path.read_text(encoding="utf-8").strip())
        if body.instructions.strip():
            parts.append(body.instructions.strip())
        if not parts:
            raise HTTPException(400, "כתוב משימה או בחר תסריט")
        agent.start("\n\n".join(parts))
        return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        await agent.stop()
        return {"ok": True}

    @app.post("/api/decision")
    async def decision(body: DecisionBody):
        ok = hub.resolve(body.id, Decision(body.approved, body.comment.strip(), body.edited_text))
        if not ok:
            raise HTTPException(409, "הבקשה כבר לא ממתינה")
        return {"ok": True}

    @app.post("/api/answer")
    async def answer(body: AnswerBody):
        if not body.answer.strip():
            raise HTTPException(400, "התשובה ריקה")
        if not hub.resolve(body.id, body.answer.strip()):
            raise HTTPException(409, "השאלה כבר לא ממתינה")
        return {"ok": True}

    @app.post("/api/note")
    async def note(body: NoteBody):
        if not agent.running:
            raise HTTPException(409, "אין משימה פעילה")
        if body.text.strip():
            hub.add_note(body.text.strip())
        return {"ok": True}

    @app.post("/api/mode")
    async def mode(body: ModeBody):
        if body.mode not in APPROVAL_MODES:
            raise HTTPException(400, "מצב לא מוכר")
        hub.mode = body.mode
        hub.log("mode", f"מצב העבודה שונה ל: {MODE_LABELS[body.mode]}")
        return {"ok": True}

    @app.post("/api/open-portal")
    async def open_portal():
        if agent.running:
            raise HTTPException(409, "יש משימה פעילה")
        try:
            await browser.open(settings.start_url)
        except ActionError as e:
            raise HTTPException(502, str(e))
        hub.set_screenshot(await browser.preview_page())
        return {"ok": True}

    @app.post("/api/refresh")
    async def refresh():
        if not agent.running and not browser.pending_dialog:
            hub.set_screenshot(await browser.preview_page())
        return {"ok": True}

    return app
