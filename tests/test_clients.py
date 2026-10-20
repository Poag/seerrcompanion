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
