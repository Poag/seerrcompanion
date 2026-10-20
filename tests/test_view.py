import re

from starlette.testclient import TestClient

from seerrcompanion.view import RECHECK, RequestView
from seerrcompanion.web import create_app
from test_logic import FakeSeerr, FakeWatch, build, req
from test_web import login
from seerrcompanion.store import Store


def view(reqs, watched=()):
    s, w = FakeSeerr(reqs), FakeWatch(watched)
    return RequestView(s, w, Store(":memory:")), w, s


def test_states_and_ordering():
    reqs = [
        req(1, day=0), req(2, day=1), req(3, status=1, day=2), req(4, status=3, day=3),
        req(5, media_status=3, day=4),
    ]
    v, _, _ = view(reqs, watched=[1])
    assert v.rows is None
    v.refresh(reqs)
    by = {r.request_id: r for r in v.rows}
    assert [r.request_id for r in v.rows] == [5, 4, 3, 2, 1]
    assert by[1].watched == "watched" and by[2].watched == "not watched"
    assert by[3].watched == by[4].watched == by[5].watched == "n/a"
    assert (by[3].status, by[4].status, by[1].availability) == ("pending", "declined", "available")


def test_watched_is_sticky_and_negative_is_rechecked_after_interval():
    reqs = [req(1), req(2)]
    v, w, _ = view(reqs, watched=[1])
    v.refresh(reqs, now=1000)
    w.w = {2}  # 1 is no longer reported watched; 2 now is
    v.refresh(reqs, now=1000 + 60)
    by = {r.request_id: r.watched for r in v.rows}
    assert by == {1: "watched", 2: "not watched"}  # 2 still cached inside the interval
    v.refresh(reqs, now=1000 + RECHECK + 1)
    assert {r.request_id: r.watched for r in v.rows}[2] == "watched"


def test_unknown_when_source_cannot_tell():
    reqs = [req(1)]
    v, w, _ = view(reqs)
    w.watched = lambda r: None
    v.refresh(reqs)
    assert v.rows[0].watched == "unknown"


def test_titles_cached_and_failures_not_kept():
    reqs = [req(1, tmdb=7)]
    v, _, s = view(reqs)
    calls = []
    s.title = lambda k, t: (calls.append(t), "Seven")[1]
    v.refresh(reqs); v.refresh(reqs)
    assert calls == [7]
    v2, _, s2 = view([req(2, tmdb=8)])
    s2.title = lambda k, t: f"tmdb:{t}"
    v2.refresh([req(2, tmdb=8)])
    assert v2._store.title("tv", 8) is None


def test_requests_page_requires_login_filters_and_shows_state(tmp_path):
    app, seerr, _ = build([req(1, day=0), req(2, status=1, seasons=(1, 2, 3), day=1)],
                          webui_username="admin", webui_password="pw", data_dir=str(tmp_path))
    c = TestClient(create_app(app), follow_redirects=False)
    assert c.get("/requests").status_code == 303
    login(c)
    assert "Loading" in c.get("/requests").text
    app.run_once()
    page = c.get("/requests").text
    assert "Show1" in page and "Show2" in page and "not watched" in page and "S1, 2, 3" in page
    pend = c.get("/requests?f=pending").text
    assert "Show2" in pend and "Show1" not in pend
    assert "Pending (1)" in page and "Approved (1)" in page
    assert c.get("/requests?f=bogus&p=zzz").status_code == 200


def _bulk(c, token, media, back="/requests"):
    body = "&".join([f"csrf={token}", f"back={back}"] + [f"m={m}" for m in media])
    return c.post("/requests/delete", content=body, headers={"content-type": "application/x-www-form-urlencoded"})


def _ui(tmp_path, **cfg):
    app, seerr, notifier = build([req(1, day=0), req(2, status=1, day=1, media_status=2), req(3, day=2)],
                                 webui_username="admin", webui_password="pw", data_dir=str(tmp_path), **cfg)
    app.run_once()
    c = TestClient(create_app(app), follow_redirects=False)
    login(c)
    return c, app, seerr, notifier, re.search(r"name=csrf value=(\w+)", c.get("/requests").text).group(1)


def test_requests_page_has_checkboxes():
    import tempfile
    c, *_ = _ui(tempfile.mkdtemp())
    page = c.get("/requests").text
    assert page.count("class=pick") == 3 and "Delete selected" in page


def test_bulk_delete_removes_files_only_when_available(tmp_path):
    c, app, seerr, notifier, token = _ui(tmp_path)
    r = _bulk(c, token, [1, 2])
    assert "m=Deleted%202" in r.headers["location"]
    assert ("file", 1) in seerr.calls and ("media", 1) in seerr.calls
    assert ("media", 2) in seerr.calls and ("file", 2) not in seerr.calls  # media 2 has no files yet
    assert [n[0] for n in notifier.sent] == ["Deleted", "Deleted"]


def test_bulk_delete_needs_csrf_login_and_known_media(tmp_path):
    c, app, seerr, _, token = _ui(tmp_path)
    assert _bulk(c, "forged", [1]).status_code == 403
    assert "err=" in _bulk(c, token, [999]).headers["location"]
    assert seerr.calls == []
    anon = TestClient(create_app(app), follow_redirects=False)
    assert _bulk(anon, token, [1]).status_code == 303 and seerr.calls == []


def test_bulk_delete_clears_flag_and_dry_run_refuses(tmp_path):
    c, app, seerr, _, token = _ui(tmp_path)
    app.seerr.reqs.append(req(4, day=3))
    app.run_once()  # flags request 3
    assert [f["request_id"] for f in app.store.flagged()] == [3]
    _bulk(c, token, [3])
    assert app.store.get_flag(3)["state"] == "purged"
    app.cfg = type(app.cfg)(**{**app.cfg.__dict__, "dry_run": True})
    r = _bulk(c, token, [4])
    assert "Dry+run" in r.headers["location"] or "Dry%20run" in r.headers["location"]
