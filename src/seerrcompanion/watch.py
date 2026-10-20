"""Watch-state sources. `watched()` returns True/False, or None when it cannot be determined."""
from __future__ import annotations

import logging
from typing import Protocol

from .models import Request
from .seerr import SeerrClient
from .tautulli import TautulliClient

log = logging.getLogger(__name__)


class WatchSource(Protocol):
    def watched(self, req: Request) -> bool | None: ...


class TautulliWatch:
    """Did the requesting user play the title after they requested it?"""

    def __init__(self, tautulli: TautulliClient):
        self._t = tautulli

    def watched(self, req: Request) -> bool | None:
        if not req.rating_key:
            log.warning("Request %s has no Plex ratingKey in Seerr; cannot check Tautulli", req.id)
            return None
        user_id = req.plex_id or self._t.user_id_for(req.user_name, req.user_email)
        if user_id is None:
            log.warning("Request %s: no Tautulli user found for %s", req.id, req.user_name)
            return None
        return self._t.play_count_since(user_id, req.kind, req.rating_key, req.created) > 0


class SeerrWatch:
    """Fallback: Seerr's own watch data. No timestamps, so it means 'ever watched by this user'."""

    def __init__(self, seerr: SeerrClient):
        self._s = seerr

    def watched(self, req: Request) -> bool | None:
        return req.user_id in self._s.users_who_watched(req.media_id)
