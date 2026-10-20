from __future__ import annotations

import logging

import httpx

from .models import Request, parse_request

log = logging.getLogger(__name__)
PAGE = 100


class SeerrClient:
    def __init__(self, url: str, api_key: str, client: httpx.Client | None = None):
        self._http = client or httpx.Client(
            base_url=f"{url}/api/v1", headers={"X-Api-Key": api_key}, timeout=30
        )

    def requests(self) -> list[Request]:
        """All requests, oldest first."""
        out: list[Request] = []
        skip = 0
        while True:
            r = self._http.get(
                "/request",
                params={"take": PAGE, "skip": skip, "filter": "all", "sort": "added", "sortDirection": "asc"},
            )
            r.raise_for_status()
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
            r.raise_for_status()
            d = r.json()
            return d.get("name") or d.get("title") or f"tmdb:{tmdb_id}"
        except httpx.HTTPError:
            return f"tmdb:{tmdb_id}"

    def set_seasons(self, request_id: int, seasons: list[int]) -> None:
        # Seerr only allows modifying pending requests (409 otherwise).
        r = self._http.put(f"/request/{request_id}", json={"mediaType": "tv", "seasons": seasons})
        r.raise_for_status()

    def approve(self, request_id: int) -> None:
        self._http.post(f"/request/{request_id}/approve").raise_for_status()

    def users_who_watched(self, media_id: int) -> set[int]:
        """Seerr's own watch data (backed by Tautulli); has no timestamps."""
        r = self._http.get(f"/media/{media_id}/watch_data")
        r.raise_for_status()
        return {u["id"] for u in (r.json().get("data") or {}).get("users", [])}

    def delete_media_file(self, media_id: int) -> None:
        self._http.delete(f"/media/{media_id}/file").raise_for_status()

    def delete_media(self, media_id: int) -> None:
        self._http.delete(f"/media/{media_id}").raise_for_status()
