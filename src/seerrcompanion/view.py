"""Read-only snapshot of every request with its watch state, for the web UI.

Built by the poller so a page load never touches Seerr or Tautulli. A positive watch result is
kept for good; a negative or unknown one is re-checked every RECHECK seconds.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .models import APPROVED, DECLINED, PENDING, Request
from .seerr import SeerrClient
from .store import Store
from .watch import WatchSource

log = logging.getLogger(__name__)
RECHECK = 15 * 60

STATUS = {PENDING: "pending", APPROVED: "approved", DECLINED: "declined"}
AVAILABILITY = {1: "unknown", 2: "pending", 3: "processing", 4: "partial", 5: "available", 6: "deleted"}


@dataclass(frozen=True)
class Row:
    request_id: int
    title: str
    kind: str
    tmdb_id: int
    user_name: str
    seasons: tuple[int, ...]
    status: str
    availability: str
    watched: str  # watched | not watched | unknown | n/a
    created: float
    flag: str | None  # flagged | kept | cleared | purged


class RequestView:
    def __init__(self, seerr: SeerrClient, watch: WatchSource, store: Store):
        self._seerr, self._watch, self._store = seerr, watch, store
        self._cache: dict[int, tuple[str, float]] = {}
        self.rows: list[Row] | None = None  # None until the first refresh finishes

    def _title(self, r: Request) -> str:
        cached = self._store.title(r.kind, r.tmdb_id)
        if cached:
            return cached
        title = self._seerr.title(r.kind, r.tmdb_id)
        if not title.startswith("tmdb:"):  # a failed lookup returns a placeholder; don't keep it
            self._store.set_title(r.kind, r.tmdb_id, title)
        return title

    def _watch_state(self, r: Request, now: float) -> str:
        if r.status != APPROVED or not r.available:
            return "n/a"
        hit = self._cache.get(r.id)
        if hit and (hit[0] == "watched" or now - hit[1] < RECHECK):
            return hit[0]
        try:
            w = self._watch.watched(r)
        except Exception:
            log.exception("Watch check failed for request %s", r.id)
            w = None
        state = "unknown" if w is None else "watched" if w else "not watched"
        self._cache[r.id] = (state, now)
        return state

    def refresh(self, reqs: list[Request], now: float | None = None) -> None:
        now = time.time() if now is None else now
        flags = {f["request_id"]: f["state"] for s in ("flagged", "kept", "cleared", "purged") for f in self._store.flagged(s)}
        rows = [
            Row(
                request_id=r.id, title=self._title(r), kind=r.kind, tmdb_id=r.tmdb_id, user_name=r.user_name,
                seasons=r.seasons, status=STATUS.get(r.status, str(r.status)),
                availability=AVAILABILITY.get(r.media_status, "unknown"), watched=self._watch_state(r, now),
                created=r.created.timestamp(), flag=flags.get(r.id),
            )
            for r in sorted(reqs, key=lambda r: r.created, reverse=True)
        ]
        self.rows = rows  # single reference swap; readers never see a half-built list
