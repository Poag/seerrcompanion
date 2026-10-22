from __future__ import annotations

import logging
from http.cookies import SimpleCookie
from http.cookiejar import DefaultCookiePolicy
from urllib.parse import unquote

import httpx

from .models import Request, parse_request

log = logging.getLogger(__name__)
PAGE = 100


class SeerrClient:
    def __init__(self, url: str, api_key: str, client: httpx.Client | None = None):
        self._http = client or httpx.Client(
            base_url=f"{url}/api/v1", headers={"X-Api-Key": api_key}, timeout=30
        )
        # Cookies are handled by hand in _mutate: Seerr marks its CSRF cookies Secure, which
        # the stdlib jar will not send back over plain http.
        self._http.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        self._csrf: dict[str, str] = {}

    @staticmethod
    def _check(r: httpx.Response) -> httpx.Response:
        """raise_for_status, but with Seerr's response body in the message."""
        if r.is_error:
            body = " ".join(r.text.split())[:300]
            raise httpx.HTTPStatusError(f"{r.status_code} {r.request.method} {r.request.url.path}: {body}", request=r.request, response=r)
        return r

    def _refresh_csrf(self) -> None:
        r = self._http.get("/status")
        jar = SimpleCookie()
        for header in r.headers.get_list("set-cookie"):
            jar.load(header)
        self._csrf = {k: unquote(m.value) for k, m in jar.items() if k in ("_csrf", "XSRF-TOKEN")}

    def _mutate(self, method: str, path: str, **kw) -> httpx.Response:
        """PUT/POST/DELETE. If Seerr has CSRF protection on it rejects these (403) even with an API
        key; fetch the CSRF cookies from a GET and retry once with the token."""
        def send() -> httpx.Response:
            headers = {}
            if self._csrf:
                headers = {
                    "Cookie": "; ".join(f"{k}={v}" for k, v in self._csrf.items()),
                    "X-XSRF-TOKEN": self._csrf.get("XSRF-TOKEN", ""),
                }
            return self._http.request(method, path, headers=headers, **kw)

        r = send()
        if r.status_code == 403 and "csrf" in r.text.lower():
            log.info("Seerr has CSRF protection enabled; retrying %s %s with a CSRF token", method, path)
            self._refresh_csrf()
            r = send()
        return self._check(r)

    def requests(self) -> list[Request]:
        """All requests, oldest first."""
        out: list[Request] = []
        skip = 0
        while True:
            r = self._http.get(
                "/request",
                params={"take": PAGE, "skip": skip, "filter": "all", "sort": "added", "sortDirection": "asc"},
            )
            self._check(r)
            results = r.json().get("results", [])
            for item in results:
                try:
                    out.append(parse_request(item))
                except (KeyError, ValueError) as e:
                    log.warning("Skipping unparseable request %s: %r", item.get("id"), e)
            if len(results) < PAGE:
                return out
            skip += PAGE

    def title(self, kind: str, tmdb_id: int) -> str:
        try:
            r = self._http.get(f"/{'tv' if kind == 'tv' else 'movie'}/{tmdb_id}")
            self._check(r)
            d = r.json()
            return d.get("name") or d.get("title") or f"tmdb:{tmdb_id}"
        except httpx.HTTPError:
            return f"tmdb:{tmdb_id}"

    def set_seasons(self, request_id: int, seasons: list[int]) -> None:
        # Seerr only allows modifying pending requests (409 otherwise).
        self._mutate("PUT", f"/request/{request_id}", json={"mediaType": "tv", "seasons": seasons})

    def approve(self, request_id: int) -> None:
        self._mutate("POST", f"/request/{request_id}/approve")

    def users_who_watched(self, media_id: int) -> set[int]:
        """Seerr's own watch data (backed by Tautulli); has no timestamps."""
        r = self._http.get(f"/media/{media_id}/watch_data")
        self._check(r)
        return {u["id"] for u in (r.json().get("data") or {}).get("users", [])}

    def delete_media_file(self, media_id: int) -> None:
        self._mutate("DELETE", f"/media/{media_id}/file")

    def delete_media(self, media_id: int) -> None:
        self._mutate("DELETE", f"/media/{media_id}")
