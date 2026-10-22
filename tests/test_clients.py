from datetime import datetime, timezone

import httpx

from seerrcompanion.seerr import SeerrClient
from seerrcompanion.tautulli import TautulliClient


def test_seerr_set_seasons_and_pagination():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.read()))
        if request.url.path.endswith("/request") and request.method == "GET":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={})

    c = SeerrClient("http://s", "k", httpx.Client(base_url="http://s/api/v1", transport=httpx.MockTransport(handler)))
    assert c.requests() == []
    c.set_seasons(4, [1])
    assert seen[-1] == ("PUT", "/api/v1/request/4", b'{"mediaType":"tv","seasons":[1]}')


def test_tautulli_counts_only_plays_after_request():
    since = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def handler(request: httpx.Request):
        assert request.url.params["grandparent_rating_key"] == "42"
        rows = [{"started": since.timestamp() - 10}, {"started": since.timestamp() + 10}]
        return httpx.Response(200, json={"response": {"result": "success", "data": {"data": rows}}})

    t = TautulliClient("http://t", "k", httpx.Client(base_url="http://t/api/v2", transport=httpx.MockTransport(handler)))
    assert t.play_count_since(7, "tv", "42", since) == 1


def _csrf_seerr(csrf_on: bool):
    """A fake Seerr that behaves like csurf: non-GET without a matching token gets 403."""
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, dict(request.headers)))
        if request.method == "GET":
            h = [("set-cookie", "_csrf=secret123; Path=/; HttpOnly; Secure; SameSite=Strict"),
                 ("set-cookie", "XSRF-TOKEN=tok%2Fen; Path=/; Secure; SameSite=Strict")]
            return httpx.Response(200, json={}, headers=h)
        if csrf_on and not (
            "_csrf=secret123" in request.headers.get("cookie", "") and request.headers.get("x-xsrf-token") == "tok/en"
        ):
            return httpx.Response(403, json={"status": 403, "message": "invalid csrf token"})
        return httpx.Response(204)

    http = httpx.Client(base_url="http://s/api/v1", transport=httpx.MockTransport(handler))
    return SeerrClient("http://s", "k", http), seen


def test_delete_retries_with_csrf_token_when_seerr_requires_it():
    c, seen = _csrf_seerr(csrf_on=True)
    c.delete_media_file(5)
    assert [m for m, _, _ in seen] == ["DELETE", "GET", "DELETE"]
    c.delete_media(5)  # token is kept, no further handshake
    assert [m for m, _, _ in seen][3:] == ["DELETE"]


def test_no_csrf_handshake_when_not_needed():
    c, seen = _csrf_seerr(csrf_on=False)
    c.approve(1)
    assert [m for m, _, _ in seen] == ["POST"]


def test_error_message_includes_response_body():
    import pytest

    def handler(request):
        return httpx.Response(403, json={"error": "You do not have permission to access this endpoint"})

    c = SeerrClient("http://s", "k", httpx.Client(base_url="http://s/api/v1", transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPStatusError, match="do not have permission"):
        c.delete_media(1)
