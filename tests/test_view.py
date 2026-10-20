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
