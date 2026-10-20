from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

log = logging.getLogger(__name__)


class TautulliClient:
    def __init__(self, url: str, api_key: str, client: httpx.Client | None = None):
        self._http = client or httpx.Client(base_url=f"{url}/api/v2", timeout=30)
        self._key = api_key
        self._users: dict[str, int] | None = None

    def _call(self, cmd: str, **params):
        r = self._http.get("", params={"apikey": self._key, "cmd": cmd, **params})
        r.raise_for_status()
        resp = r.json()["response"]
        if resp.get("result") != "success":
            raise RuntimeError(f"Tautulli {cmd} failed: {resp.get('message')}")
        return resp["data"]

    def user_id_for(self, *names: str | None) -> int | None:
        """Resolve a Tautulli user id from a username/email, for Seerr users without a plexId."""
        if self._users is None:
            self._users = {}
            for u in self._call("get_users"):
                for key in (u.get("username"), u.get("friendly_name"), u.get("email")):
                    if key:
                        self._users[key.lower()] = u["user_id"]
        for n in names:
            if n and n.lower() in self._users:
                return self._users[n.lower()]
        return None

    def play_count_since(self, user_id: int, kind: str, rating_key: str, since: datetime) -> int:
        """Plays by `user_id` of the movie / any episode of the show since `since`."""
        key = "rating_key" if kind == "movie" else "grandparent_rating_key"
        data = self._call(
            "get_history",
            user_id=user_id,
            length=1000,
            after=(since - timedelta(days=1)).strftime("%Y-%m-%d"),
            **{key: rating_key},
        )
        cutoff = since.timestamp()
        return sum(1 for row in data.get("data", []) if (row.get("started") or 0) >= cutoff)
