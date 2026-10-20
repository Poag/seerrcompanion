"""Small admin UI: review titles marked for deletion, then approve or keep them."""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import secrets
import time
from datetime import datetime
from urllib.parse import parse_qs, quote

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from .logic import Companion

log = logging.getLogger(__name__)

COOKIE = "seerrcompanion_session"
SESSION_SECONDS = 7 * 86400
MAX_FAILS, FAIL_WINDOW = 5, 300

CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#d9d9de;--card:#f6f6f8;--acc:#3b6ef5;--bad:#c62828}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--mut:#9a9aa3;--line:#33333a;--card:#1f1f23;--acc:#7b9bff;--bad:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}h1{font-size:20px}h2{font-size:16px;margin-top:32px}
.top{display:flex;justify-content:space-between;align-items:center}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0;display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.card small{color:var(--mut);display:block}.acts{display:flex;gap:8px}
button{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}
button.del{border-color:var(--bad);color:var(--bad)}button.keep{border-color:var(--acc);color:var(--acc)}
input{font:inherit;padding:8px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg);width:100%;margin:4px 0 12px}
a{color:var(--acc)}.msg{padding:8px 12px;border-radius:8px;border:1px solid var(--line);margin:10px 0}.err{border-color:var(--bad);color:var(--bad)}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style><main>{body}</main></html>"
    )


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def create_app(companion: Companion) -> Starlette:
    cfg, store = companion.cfg, companion.store
    secret = _load_secret(cfg.data_dir)
    fails: dict[str, list[float]] = {}

    def sign(msg: str) -> str:
        return hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()

    def session_of(request: Request) -> str | None:
        raw = request.cookies.get(COOKIE, "")
        try:
            user, expiry, sig = raw.rsplit("|", 2)
            if hmac.compare_digest(sig, sign(f"{user}|{expiry}")) and float(expiry) > time.time() and user == cfg.webui_username:
                return raw
        except ValueError:
            pass
        return None

    def csrf(session: str) -> str:
        return sign("csrf|" + session)

    async def form(request: Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}

    def client(request: Request) -> str:
        return request.client.host if request.client else "?"

    async def login_page(request: Request) -> Response:
        msg = '<div class="msg err">Wrong username or password.</div>' if request.query_params.get("e") else ""
        return _page("Sign in", f"""<h1>seerrcompanion</h1>{msg}
<form method=post action=/login><label>Username<input name=username autocomplete=username required autofocus></label>
<label>Password<input name=password type=password autocomplete=current-password required></label><button class=keep>Sign in</button></form>""")

    async def login(request: Request) -> Response:
        ip, now = client(request), time.time()
        recent = [t for t in fails.get(ip, []) if now - t < FAIL_WINDOW]
        fails[ip] = recent
        if len(recent) >= MAX_FAILS:
            return Response("Too many attempts; try again in a few minutes.", status_code=429)
        f = await form(request)
        ok_user = hmac.compare_digest(f.get("username", "").encode(), (cfg.webui_username or "").encode())
        ok_pass = hmac.compare_digest(f.get("password", "").encode(), (cfg.webui_password or "").encode())
        if not (ok_user and ok_pass):
            fails[ip].append(now)
            log.warning("Failed web UI login from %s", ip)
            return RedirectResponse("/login?e=1", status_code=303)
        expiry = str(now + SESSION_SECONDS)
        value = f"{cfg.webui_username}|{expiry}"
        resp = RedirectResponse("/", status_code=303)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(COOKIE, f"{value}|{sign(value)}", max_age=SESSION_SECONDS, httponly=True, samesite="strict", secure=secure)
        return resp

    async def logout(request: Request) -> Response:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    def seerr_link(row) -> str:
        base = cfg.seerr_public_url or cfg.seerr_url
        return f"{base}/tv/{row['tmdb_id']}"

    async def index(request: Request) -> Response:
        session = session_of(request)
        if not session:
            return RedirectResponse("/login", status_code=303)
        token = csrf(session)
        notice = request.query_params.get("m", "")
        err = request.query_params.get("err")
        out = ["<div class=top><h1>Marked for deletion</h1><form method=post action=/logout><button>Sign out</button></form></div>"]
        if cfg.dry_run:
            out.append('<div class="msg">Dry run: nothing is recorded or deleted. Set DRY_RUN=false to go live.</div>')
        if notice:
            out.append(f'<div class="msg">{html.escape(notice)}</div>')
        if err:
            out.append(f'<div class="msg err">{html.escape(err)}</div>')
        rows = store.flagged()
        if not rows:
            out.append("<p>Nothing is waiting for review.</p>")
        for r in rows:
            rid = r["request_id"]
            out.append(
                f"<div class=card><div><a href='{html.escape(seerr_link(r))}'>{html.escape(r['title'])}</a>"
                f"<small>Requested by {html.escape(r['user_name'])}, flagged {_ts(r['flagged_at'])}</small></div><div class=acts>"
                + "".join(
                    f"<form method=post action=/flags/{rid}/{act}><input type=hidden name=csrf value={token}>"
                    f"<button class={cls} {confirm}>{label}</button></form>"
                    for act, cls, label, confirm in (
                        ("keep", "keep", "Keep", ""),
                        ("delete", "del", "Delete", "onclick=\"return confirm('Delete this title and its files?')\""),
                    )
                )
                + "</div></div>"
            )
        hist = store.history()
        if hist:
            out.append("<h2>History</h2><table><tr><th>Title<th>User<th>Flagged<th>Outcome</tr>")
            for r in hist:
                out.append(f"<tr><td>{html.escape(r['title'])}<td>{html.escape(r['user_name'])}<td>{_ts(r['flagged_at'])}<td>{html.escape(r['state'])}</tr>")
            out.append("</table>")
        return _page("seerrcompanion", "".join(out))

    async def act(request: Request) -> Response:
        session = session_of(request)
        if not session:
            return RedirectResponse("/login", status_code=303)
        f = await form(request)
        if not hmac.compare_digest(f.get("csrf", ""), csrf(session)):
            return Response("Bad CSRF token", status_code=403)
        rid, action = int(request.path_params["rid"]), request.path_params["action"]
        try:
            if action == "delete":
                companion.delete_flagged(rid, reason="approved in the web UI")
                msg = "Deleted."
            else:
                companion.keep_flagged(rid)
                msg = "Kept; it will not be flagged again."
        except ValueError as e:
            return RedirectResponse(f"/?err={quote(str(e))}", status_code=303)
        except httpx.HTTPError as e:
            log.exception("Seerr delete failed")
            return RedirectResponse(f"/?err={quote('Seerr refused the delete: ' + str(e))}", status_code=303)
        return RedirectResponse(f"/?m={quote(msg)}", status_code=303)

    return Starlette(routes=[
        Route("/", index),
        Route("/login", login_page, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/flags/{rid:int}/{action:str}", act, methods=["POST"]),
    ])


def _load_secret(data_dir: str) -> bytes:
    env = os.environ.get("WEBUI_SECRET")
    if env:
        return env.encode()
    path = os.path.join(data_dir, "webui.secret")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        pass
    secret = secrets.token_hex(32).encode()
    try:
        with open(path, "wb") as fh:
            fh.write(secret)
        os.chmod(path, 0o600)
    except OSError:
        log.warning("Could not persist the web UI secret; sessions will reset on restart")
    return secret
