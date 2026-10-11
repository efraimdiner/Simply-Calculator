"""A fake doctors-dispatch portal for trying the agent safely. All data is invented."""

from __future__ import annotations

import html
from copy import deepcopy
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

DOCTORS = ["ד״ר אבי כהן", "ד״ר מיכל לוי", "ד״ר יוסף מזרחי", "ד״ר רונית פרץ", "ד״ר דני ביטון"]

INITIAL_VISITS = [
    {"id": 1041, "patient": "ישראל ישראלי", "city": "חיפה", "address": "הנביאים 12", "complaint": "חום גבוה ושיעול", "status": "חדשה", "doctor": ""},
    {"id": 1042, "patient": "שרה אברהם", "city": "קריית אתא", "address": "העצמאות 4", "complaint": "כאבי בטן", "status": "חדשה", "doctor": ""},
    {"id": 1043, "patient": "משה דוד", "city": "חיפה", "address": "מוריה 88", "complaint": "פריחה בעור", "status": "שובצה", "doctor": "ד״ר מיכל לוי"},
    {"id": 1044, "patient": "רחל יעקב", "city": "נשר", "address": "הדקל 3", "complaint": "סחרחורת", "status": "חדשה", "doctor": ""},
]

PAGE = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:Arial,sans-serif;margin:24px;background:#f7f7f7}} table{{border-collapse:collapse;background:#fff}}
td,th{{border:1px solid #ccc;padding:6px 10px}} .flash{{background:#e6f4ee;padding:8px;margin-bottom:12px}}
form.box{{background:#fff;padding:16px;max-width:520px}} label{{display:block;margin-top:10px}}</style></head>
<body><h1>{title}</h1>{body}</body></html>"""


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    app.state.visits = deepcopy(INITIAL_VISITS)
    e = html.escape

    def page(title: str, body: str) -> HTMLResponse:
        return HTMLResponse(PAGE.format(title=e(title), body=body))

    def logged_in(request: Request) -> bool:
        return request.cookies.get("session") == "ok"

    async def form(request: Request) -> dict[str, str]:
        data = parse_qs((await request.body()).decode())
        return {k: v[0] for k, v in data.items()}

    def find(visit_id: int) -> dict | None:
        return next((v for v in app.state.visits if v["id"] == visit_id), None)

    @app.get("/")
    async def root(request: Request):
        return RedirectResponse("/visits" if logged_in(request) else "/login", status_code=303)

    @app.get("/login")
    async def login_page():
        return page("כניסה לפורטל", """<form class="box" method="post" action="/login">
            <label>שם משתמש <input name="username"></label>
            <label>סיסמה <input name="password" type="password"></label>
            <p><button type="submit">כניסה</button></p></form>""")

    @app.post("/login")
    async def login(request: Request):
        data = await form(request)
        if not data.get("username") or not data.get("password"):
            return RedirectResponse("/login", status_code=303)
        resp = RedirectResponse("/visits", status_code=303)
        resp.set_cookie("session", "ok")
        return resp

    @app.get("/visits")
    async def visits(request: Request, saved: int | None = None):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        rows = "".join(
            f"<tr><td>{v['id']}</td><td>{e(v['patient'])}</td><td>{e(v['city'])}</td><td>{e(v['address'])}</td>"
            f"<td>{e(v['complaint'])}</td><td>{e(v['status'])}</td><td>{e(v['doctor']) or '—'}</td>"
            f"<td><a href=\"/visits/{v['id']}\">פתח קריאה</a></td></tr>"
            for v in app.state.visits
        )
        flash = f'<div class="flash">קריאה {saved} נשמרה</div>' if saved else ""
        return page("קריאות פתוחות", flash + "<table><tr><th>מס׳</th><th>מטופל</th><th>עיר</th><th>כתובת</th>"
                    "<th>תלונה</th><th>סטטוס</th><th>רופא</th><th></th></tr>" + rows + "</table>")

    @app.get("/visits/{visit_id}")
    async def visit(request: Request, visit_id: int):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        v = find(visit_id)
        if not v:
            return page("לא נמצא", "<p>הקריאה לא נמצאה</p>")
        options = '<option value="">בחר רופא…</option>' + "".join(
            f'<option{" selected" if d == v["doctor"] else ""}>{e(d)}</option>' for d in DOCTORS
        )
        return page(f"קריאה {v['id']}", f"""
            <p>מטופל: {e(v['patient'])} | {e(v['address'])}, {e(v['city'])} | תלונה: {e(v['complaint'])}</p>
            <form class="box" method="post" action="/visits/{v['id']}"
                  onsubmit="return confirm('לשמור את השיבוץ לקריאה {v['id']}?')">
              <label for="doctor">רופא מטפל</label>
              <select id="doctor" name="doctor">{options}</select>
              <label for="notes">הערות למוקד</label>
              <textarea id="notes" name="notes" rows="3"></textarea>
              <p><button type="submit">שמור שיבוץ</button> <a href="/visits">חזרה לרשימה</a></p>
            </form>""")

    @app.post("/visits/{visit_id}")
    async def save_visit(request: Request, visit_id: int):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        v = find(visit_id)
        data = await form(request)
        if v and data.get("doctor") in DOCTORS:
            v["doctor"] = data["doctor"]
            v["status"] = "שובצה"
            v["notes"] = data.get("notes", "")
        return RedirectResponse(f"/visits?saved={visit_id}", status_code=303)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8100)
