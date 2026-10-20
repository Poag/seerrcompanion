from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Seerr request status
PENDING = 1
APPROVED = 2
DECLINED = 3

# Seerr media availability status
PARTIALLY_AVAILABLE = 4
AVAILABLE = 5


@dataclass(frozen=True)
class Request:
    id: int
    status: int
    kind: str  # "tv" or "movie"
    tmdb_id: int
    media_id: int
    media_status: int
    rating_key: str | None
    user_id: int
    plex_id: int | None
    user_name: str
    user_email: str | None
    created: datetime
    seasons: tuple[int, ...]

    @property
    def available(self) -> bool:
        return self.media_status in (PARTIALLY_AVAILABLE, AVAILABLE)


def parse_request(d: dict) -> Request:
    media = d.get("media") or {}
    user = d.get("requestedBy") or {}
    kind = d.get("type") or media.get("mediaType") or "movie"
    rating_key = media.get("ratingKey")
    plex_id = user.get("plexId")
    name = (
        user.get("displayName")
        or user.get("plexUsername")
        or user.get("username")
        or user.get("email")
        or f"user {user.get('id')}"
    )
    return Request(
        id=d["id"],
        status=d["status"],
        kind=kind,
        tmdb_id=media.get("tmdbId") or 0,
        media_id=media.get("id") or 0,
        media_status=media.get("status") or 0,
        rating_key=str(rating_key) if rating_key else None,
        user_id=user.get("id") or 0,
        plex_id=int(plex_id) if plex_id else None,
        user_name=name,
        user_email=user.get("email"),
        created=datetime.fromisoformat(d["createdAt"].replace("Z", "+00:00")),
        seasons=tuple(sorted(s["seasonNumber"] for s in d.get("seasons") or [])),
    )
