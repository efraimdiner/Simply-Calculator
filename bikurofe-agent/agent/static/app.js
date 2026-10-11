"use strict";

const STATUS = {
  idle: ["ממתין למשימה", ""],
  running: ["הסוכן עובד…", "running"],
  waiting_approval: ["ממתין לאישור שלך", "waiting"],
  waiting_answer: ["ממתין לתשובה שלך", "waiting"],
};

const KINDS = {
  task: "משימה", thinking: "חשיבה", agent: "הסוכן", proposal: "מבקש אישור",
  approved: "אושר", rejected: "נדחה", action: "בוצע אוטומטית", question: "שאלה",
  answer: "התשובה שלך", note: "הערה שלך", error: "שגיאה", done: "סיום",
  stopped: "נעצר", mode: "מצב",
};

const $ = (id) => document.getElementById(id);
let lastEntry = 0;
let shotVersion = -1;
let pendingId = null;
let modesLoaded = false;
const baseTitle = document.title;

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Dashboard": "1" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* keep status text */ }
    throw new Error(msg);
  }
  return res.json();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function alertUser(text) {
  document.title = "⚠ " + text;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.value = 0.08;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.25);
  } catch (_) { /* sound is optional */ }
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    new Notification("סוכן משמרת", { body: text });
  }
}

function renderPending(p) {
  const box = $("pending");
  if (!p) {
    box.classList.add("hidden");
    box.replaceChildren();
    pendingId = null;
    document.title = baseTitle;
    return;
  }
  if (p.id === pendingId) return;
  pendingId = p.id;
  box.classList.remove("hidden");

  if (p.kind === "question") {
    const answer = el("textarea", { rows: "3", placeholder: "כתוב תשובה לסוכן" });
    const send = el("button", { class: "btn primary big", type: "submit" }, "שלח תשובה");
    const form = el("form", {
      onsubmit: async (e) => {
        e.preventDefault();
        send.disabled = true;
        try { await api("/api/answer", { id: p.id, answer: answer.value }); poll(); }
        catch (err) { alert(err.message); send.disabled = false; }
      },
    }, answer, el("div", { class: "actions" }, send));
    box.replaceChildren(
      el("div", { class: "head" }, el("span", { class: "tag" }, "שאלה מהסוכן")),
      el("h3", {}, p.title),
      form,
    );
    alertUser("הסוכן שואל שאלה");
    answer.focus();
    return;
  }

  const table = el("table", { class: "details" });
  for (const d of p.details) {
    if (d.value) table.append(el("tr", {}, el("th", {}, d.label), el("td", {}, d.value)));
  }
  let edited = null;
  if (p.editable_text !== null) {
    edited = el("textarea", { rows: "3" });
    edited.value = p.editable_text;
  }
  const comment = el("input", { type: "text", placeholder: "הערה לסוכן (מומלץ כשדוחים, כדי שידע מה לשנות)" });
  const approve = el("button", { class: "btn ok big", type: "button" }, "✓ אשר וביצוע");
  const reject = el("button", { class: "btn danger big", type: "button" }, "✗ דחה");

  async function decide(approved) {
    approve.disabled = reject.disabled = true;
    try {
      await api("/api/decision", {
        id: p.id, approved, comment: comment.value,
        edited_text: edited ? edited.value : null,
      });
      poll();
    } catch (err) {
      alert(err.message);
      approve.disabled = reject.disabled = false;
    }
  }
  approve.addEventListener("click", () => decide(true));
  reject.addEventListener("click", () => decide(false));

  const img = p.has_image
    ? el("a", { href: "/api/pending-image?id=" + p.id, target: "_blank", rel: "noopener" },
        el("img", { class: "shot", src: "/api/pending-image?id=" + p.id, alt: "המקום בדף שעליו תבוצע הפעולה" }))
    : el("p", { class: "muted" }, "אין צילום מסך לפעולה הזו.");

  box.replaceChildren(
    el("div", { class: "head" }, el("span", { class: "tag" }, "מבקש אישור"), el("h3", {}, p.title)),
    el("div", { class: "reason" }, p.reason),
    el("div", { class: "pending-grid" },
      el("div", {},
        table,
        edited ? el("label", {}, "הטקסט שיוקלד (אפשר לתקן לפני האישור)") : null,
        edited,
        el("label", {}, "הערה"),
        comment,
        el("div", { class: "actions" }, approve, reject),
      ),
      img,
    ),
  );
  alertUser("הסוכן מבקש אישור");
}

function renderEntries(entries) {
  const log = $("log");
  for (const e of entries) {
    const li = el("li", { class: e.kind },
      el("span", { class: "time" }, fmtTime(e.ts)),
      el("div", {}, el("span", { class: "kind " + e.kind }, KINDS[e.kind] || e.kind), el("span", { class: "text" }, e.text)),
      e.detail ? el("div", { class: "detail" }, e.detail) : null,
    );
    log.prepend(li);
    lastEntry = Math.max(lastEntry, e.id);
  }
}

function renderModes(state) {
  const select = $("mode");
  if (!modesLoaded) {
    for (const m of state.modes) select.append(el("option", { value: m.id }, m.label));
    modesLoaded = true;
  }
  if (document.activeElement !== select) select.value = state.mode;
}

async function poll() {
  try {
    const res = await fetch("/api/state?since=" + lastEntry);
    const state = await res.json();
    const [label, cls] = STATUS[state.status] || [state.status, ""];
    const pill = $("status");
    pill.textContent = label;
    pill.className = "pill " + cls;
    const busy = state.status !== "idle";
    $("stop-btn").disabled = !busy;
    $("start-btn").disabled = busy;
    $("open-portal").disabled = busy;
    renderModes(state);
    renderEntries(state.entries);
    renderPending(state.pending);
    if (state.screenshot_version !== shotVersion) {
      shotVersion = state.screenshot_version;
      if (shotVersion > 0) {
        $("live").src = "/api/screenshot?v=" + shotVersion;
        $("live-empty").classList.add("hidden");
      }
    }
  } catch (_) {
    $("status").textContent = "אין חיבור לסוכן";
    $("status").className = "pill waiting";
  }
}

async function loadPlaybooks() {
  const res = await fetch("/api/playbooks");
  for (const p of await res.json()) $("playbook").append(el("option", { value: p.name }, p.title));
}

$("task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/task", { instructions: $("instructions").value, playbook: $("playbook").value });
    $("instructions").value = "";
    poll();
  } catch (err) { alert(err.message); }
});

$("note-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/note", { text: $("note").value }); $("note").value = ""; poll(); }
  catch (err) { alert(err.message); }
});

$("stop-btn").addEventListener("click", async () => {
  if (!confirm("לעצור את הסוכן עכשיו?")) return;
  try { await api("/api/stop"); poll(); } catch (err) { alert(err.message); }
});

$("mode").addEventListener("change", async (e) => {
  const mode = e.target.value;
  if (mode === "autonomous" && !confirm("במצב אוטונומי הסוכן יבצע פעולות בלי לשאול. להמשיך?")) {
    poll();
    return;
  }
  try { await api("/api/mode", { mode }); poll(); } catch (err) { alert(err.message); }
});

$("open-portal").addEventListener("click", async () => {
  try { await api("/api/open-portal"); poll(); } catch (err) { alert(err.message); }
});

$("refresh-btn").addEventListener("click", async () => {
  try { await api("/api/refresh"); poll(); } catch (err) { alert(err.message); }
});

$("show-thinking").addEventListener("change", (e) => {
  $("log").classList.toggle("hide-thinking", !e.target.checked);
});
$("log").classList.add("hide-thinking");

$("notify-btn").addEventListener("click", async () => {
  if (!("Notification" in window)) return alert("הדפדפן לא תומך בהתראות");
  const perm = await Notification.requestPermission();
  $("notify-btn").textContent = perm === "granted" ? "🔔 התראות פעילות" : "🔕 התראות חסומות";
});

loadPlaybooks();
poll();
setInterval(poll, 1000);
