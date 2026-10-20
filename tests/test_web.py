import re

import pytest
from starlette.testclient import TestClient

from seerrcompanion.web import create_app
from test_logic import build, req


@pytest.fixture
def ui(tmp_path):
    app, seerr, notifier = build(
        [req(1, day=0)], webui_username="admin", webui_password="pw", data_dir=str(tmp_path), purge_after_days=None
    )
    app.run_once()
    seerr.reqs.append(req(2, day=1))
    app.run_once()  # flags request 1
    return TestClient(create_app(app), follow_redirects=False), app, seerr, notifier


def login(c, pw="pw"):
    return c.post("/login", content=f"username=admin&password={pw}", headers={"content-type": "application/x-www-form-urlencoded"})


def csrf_of(c):
    return re.search(r"name=csrf value=(\w+)", c.get("/").text).group(1)


def post(c, path, token):
    return c.post(path, content=f"csrf={token}", headers={"content-type": "application/x-www-form-urlencoded"})


def test_requires_login(ui):
    c = ui[0]
    assert c.get("/").status_code == 303
    assert post(c, "/flags/1/delete", "x").status_code == 303


def test_wrong_password_rejected_and_throttled(ui):
    c = ui[0]
    for _ in range(5):
        assert login(c, "nope").headers["location"] == "/login?e=1"
    assert login(c, "pw").status_code == 429


def test_lists_flagged_and_keep(ui):
    c, app, seerr, _ = ui
    assert login(c).status_code == 303
    assert "Show1" in c.get("/").text
    assert post(c, "/flags/1/keep", csrf_of(c)).status_code == 303
    assert app.store.flagged() == [] and app.store.get_flag(1)["state"] == "kept"
    assert seerr.calls == []
    seerr.reqs.append(req(3, day=2))
    app.run_once()  # request 2 is now judged; kept request 1 is not flagged again
    assert [r["request_id"] for r in app.store.flagged()] == [2]


def test_delete_requires_csrf_then_deletes(ui):
    c, app, seerr, notifier = ui
    login(c)
    assert post(c, "/flags/1/delete", "forged").status_code == 403
    assert seerr.calls == []
    assert post(c, "/flags/1/delete", csrf_of(c)).status_code == 303
    assert ("file", 1) in seerr.calls and ("media", 1) in seerr.calls
    assert app.store.get_flag(1)["state"] == "purged" and notifier.sent[-1][0] == "Deleted"


def test_cannot_delete_unflagged(ui):
    c, _, seerr, _ = ui
    login(c)
    r = post(c, "/flags/99/delete", csrf_of(c))
    assert "err=" in r.headers["location"] and seerr.calls == []


def test_tampered_cookie_rejected(ui):
    c = ui[0]
    login(c)
    c.cookies.set("seerrcompanion_session", "admin|9999999999999|deadbeef")
    assert c.get("/").status_code == 303


def test_icon_is_public(ui):
    r = ui[0].get("/icon.svg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and b"<svg" in r.content
