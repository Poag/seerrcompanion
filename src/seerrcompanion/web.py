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
from importlib import resources
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
a{color:var(--acc)}.bar{margin:8px 0;min-height:34px}button:disabled{opacity:.4;cursor:default}.msg{padding:8px 12px;border-radius:8px;border:1px solid var(--line);margin:10px 0}.err{border-color:var(--bad);color:var(--bad)}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
nav{display:flex;gap:16px;align-items:center;margin-bottom:8px}nav a{text-decoration:none;color:var(--mut)}nav a.on{color:var(--fg);font-weight:600}nav form{margin-left:auto}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.filters a{padding:2px 10px;border:1px solid var(--line);border-radius:99px;text-decoration:none}.filters a.on{background:var(--acc);color:var(--bg);border-color:var(--acc)}
.wrap{overflow-x:auto}.tag{padding:1px 8px;border-radius:99px;border:1px solid var(--line);white-space:nowrap}.ok{color:var(--acc);border-color:var(--acc)}.bad{color:var(--bad);border-color:var(--bad)}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<link rel=icon type=image/svg+xml href=/icon.svg><title>{html.escape(title)}</title><style>{CSS}</style><main>{body}</main></html>"
    )


BULK_JS = """<script>
(function(){var all=document.getElementById('all'),btn=document.getElementById('delsel'),cnt=document.getElementById('count');
function picks(){return document.querySelectorAll('.pick')}
function upd(){var n=document.querySelectorAll('.pick:checked').length;btn.disabled=!n;cnt.textContent=n?n+' selected':''}
if(all)all.addEventListener('change',function(){picks().forEach(function(c){c.checked=all.checked});upd()});
picks().forEach(function(c){c.addEventListener('change',upd)});upd()})();
</script>"""


def _nav(active: str) -> str:
    def a(href, label, key):
        return f"<a href={href}{' class=on' if key == active else ''}>{label}</a>"
    return (f"<nav>{a('/', 'Review', 'review')}{a('/requests', 'Requests', 'requests')}"
            "<form method=post action=/logout><button>Sign out</button></form></nav>")


def _seasons(seasons: tuple[int, ...]) -> str:
    if not seasons:
        return ""
    nums = [s for s in seasons if s > 0] or list(seasons)
    return ("S" + ", ".join(str(n) for n in nums)) if len(nums) < 8 else f"S{nums[0]}-{nums[-1]} ({len(nums)})"


FILTERS = (
    ("all", "All", lambda r: True),
    ("pending", "Pending", lambda r: r.status == "pending"),
    ("approved", "Approved", lambda r: r.status == "approved"),
    ("declined", "Declined", lambda r: r.status == "declined"),
    ("unwatched", "Not watched", lambda r: r.watched == "not watched"),
    ("flagged", "Flagged", lambda r: r.flag == "flagged"),
)
PAGE_SIZE = 50
MAX_BULK = 100


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

    async def form_multi(request: Request) -> dict[str, list[str]]:
        return parse_qs((await request.body()).decode())

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
        out = [_nav("review"), "<h1>Marked for deletion</h1>"]
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

    async def requests_page(request: Request) -> Response:
        session = session_of(request)
        if not session:
            return RedirectResponse("/login", status_code=303)
        rows = companion.view.rows
        out = [_nav("requests"), "<h1>Requests</h1>"]
        if rows is None:
            out.append("<p>Loading the first snapshot from Seerr; refresh in a moment.</p>")
            return _page("Requests", "".join(out))
        key = request.query_params.get("f", "all")
        flt = {k: fn for k, _, fn in FILTERS}.get(key, FILTERS[0][2])
        key = key if any(key == k for k, _, _ in FILTERS) else "all"
        shown = [r for r in rows if flt(r)]
        pages = max(1, -(-len(shown) // PAGE_SIZE))
        try:
            page = min(max(int(request.query_params.get("p", "1")), 1), pages)
        except ValueError:
            page = 1
        out.append("<div class=filters>" + "".join(
            f"<a href='/requests?f={k}'{' class=on' if k == key else ''}>{label} ({sum(1 for r in rows if fn(r))})</a>"
            for k, label, fn in FILTERS) + "</div>")
        token = csrf(session)
        notice, err = request.query_params.get("m"), request.query_params.get("err")
        if notice:
            out.append(f'<div class="msg">{html.escape(notice)}</div>')
        if err:
            out.append(f'<div class="msg err">{html.escape(err)}</div>')
        out.append(
            f"<form method=post action=/requests/delete id=bulk><input type=hidden name=csrf value={token}>"
            f"<input type=hidden name=back value='/requests?f={key}&p={page}'>"
            "<div class=bar><button class=del id=delsel disabled "
            "onclick=\"return confirm('Delete the '+document.querySelectorAll('.pick:checked').length+' selected title(s) and their files?')\">"
            "Delete selected</button> <span id=count></span></div>"
        )
        out.append("<div class=wrap><table><tr><th><input type=checkbox id=all aria-label='Select all on this page'><th>Title<th>User<th>Seasons<th>Status<th>Available<th>Watched<th>Requested</tr>")
        base = cfg.seerr_public_url or cfg.seerr_url
        for r in shown[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]:
            watched_cls = {"watched": "tag ok", "not watched": "tag bad"}.get(r.watched, "tag")
            flag = f" <span class='tag bad'>{html.escape(r.flag)}</span>" if r.flag in ("flagged", "kept") else ""
            out.append(
                f"<tr><td><input type=checkbox class=pick name=m value={r.media_id} aria-label='Select {html.escape(r.title)}'>"
                f"<td><a href='{html.escape(base)}/{'tv' if r.kind == 'tv' else 'movie'}/{r.tmdb_id}'>{html.escape(r.title)}</a>{flag}"
                f"<td>{html.escape(r.user_name)}<td>{html.escape(_seasons(r.seasons))}<td>{html.escape(r.status)}"
                f"<td>{html.escape(r.availability)}<td><span class='{watched_cls}'>{html.escape(r.watched)}</span><td>{_ts(r.created)}</tr>"
            )
        out.append("</table></div></form>")
        out.append(BULK_JS)
        if not shown:
            out.append("<p>No requests match.</p>")
        if pages > 1:
            nav = []
            if page > 1:
                nav.append(f"<a href='/requests?f={key}&p={page - 1}'>Previous</a>")
            nav.append(f"Page {page} of {pages}")
            if page < pages:
                nav.append(f"<a href='/requests?f={key}&p={page + 1}'>Next</a>")
            out.append("<p>" + " &middot; ".join(nav) + "</p>")
        out.append("<p><small>Watch state is shown only for approved, available titles and is re-checked every 15 minutes.</small></p>")
        return _page("Requests", "".join(out))

    async def bulk_delete(request: Request) -> Response:
        session = session_of(request)
        if not session:
            return RedirectResponse("/login", status_code=303)
        f = await form_multi(request)
        if not hmac.compare_digest(f.get("csrf", [""])[0], csrf(session)):
            return Response("Bad CSRF token", status_code=403)
        back = f.get("back", ["/requests"])[0]
        back = back if back.startswith("/requests") else "/requests"
        sep = "&" if "?" in back else "?"
        try:
            wanted = {int(m) for m in f.get("m", [])}
        except ValueError:
            return RedirectResponse(f"{back}{sep}err={quote('Bad selection')}", status_code=303)
        # Only media currently in the snapshot can be deleted; never an arbitrary id from the form.
        known: dict[int, object] = {}
        for r in companion.view.rows or []:
            known.setdefault(r.media_id, r)
        chosen = [known[m] for m in wanted if m in known][:MAX_BULK]
        if not chosen:
            return RedirectResponse(f"{back}{sep}err={quote('Nothing selected')}", status_code=303)
        done, failed = 0, []
        for r in chosen:
            try:
                companion.delete_media(r.media_id, r.title, available=r.availability in ("available", "partial"))
                done += 1
            except ValueError as e:
                return RedirectResponse(f"{back}{sep}err={quote(str(e))}", status_code=303)
            except httpx.HTTPError as e:
                log.exception("Seerr delete failed for media %s", r.media_id)
                failed.append(f"{r.title} ({e})")
        msg = f"Deleted {done} title(s)."
        if failed:
            return RedirectResponse(f"{back}{sep}m={quote(msg)}&err={quote('Failed: ' + '; '.join(failed))}", status_code=303)
        return RedirectResponse(f"{back}{sep}m={quote(msg)}", status_code=303)

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

    async def icon(request: Request) -> Response:
        data = resources.files("seerrcompanion").joinpath("static/icon.svg").read_bytes()
        return Response(data, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    return Starlette(routes=[
        Route("/icon.svg", icon),
        Route("/", index),
        Route("/requests", requests_page),
        Route("/requests/delete", bulk_delete, methods=["POST"]),
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
