"""Configuration, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_set(name: str) -> frozenset[int]:
    raw = os.environ.get(name, "")
    return frozenset(int(p) for p in raw.replace(" ", "").split(",") if p)


@dataclass(frozen=True)
class Config:
    seerr_url: str
    seerr_api_key: str
    tautulli_url: str | None = None
    tautulli_api_key: str | None = None
    apprise_urls: tuple[str, ...] = ()
    poll_interval: int = 60
    dry_run: bool = True
    trim_seasons: bool = True
    approve_after_trim: bool = True
    flag_unwatched: bool = True
    purge_after_days: int | None = None
    ignore_user_ids: frozenset[int] = field(default_factory=frozenset)
    data_dir: str = "/config"
    seerr_public_url: str | None = None
    webui_username: str | None = None
    webui_password: str | None = None
    webui_port: int = 8080
    webui_url: str | None = None  # public base URL of this UI, used in notification links

    @property
    def webui_enabled(self) -> bool:
        return bool(self.webui_username and self.webui_password)

    @property
    def use_tautulli(self) -> bool:
        return bool(self.tautulli_url and self.tautulli_api_key)

    @classmethod
    def from_env(cls) -> "Config":
        missing = [k for k in ("SEERR_URL", "SEERR_API_KEY") if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
        purge = os.environ.get("PURGE_AFTER_DAYS", "").strip()
        urls = os.environ.get("APPRISE_URLS", "")
        return cls(
            seerr_url=os.environ["SEERR_URL"].rstrip("/"),
            seerr_api_key=os.environ["SEERR_API_KEY"],
            tautulli_url=(os.environ.get("TAUTULLI_URL") or "").rstrip("/") or None,
            tautulli_api_key=os.environ.get("TAUTULLI_API_KEY") or None,
            apprise_urls=tuple(u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()),
            poll_interval=int(os.environ.get("POLL_INTERVAL", "60")),
            dry_run=_bool("DRY_RUN", True),
            trim_seasons=_bool("TRIM_SEASONS", True),
            approve_after_trim=_bool("APPROVE_AFTER_TRIM", True),
            flag_unwatched=_bool("FLAG_UNWATCHED", True),
            purge_after_days=int(purge) if purge else None,
            ignore_user_ids=_int_set("IGNORE_USER_IDS"),
            data_dir=os.environ.get("DATA_DIR", "/config"),
            seerr_public_url=(os.environ.get("SEERR_PUBLIC_URL") or "").rstrip("/") or None,
            webui_username=os.environ.get("WEBUI_USERNAME") or None,
            webui_password=os.environ.get("WEBUI_PASSWORD") or None,
            webui_port=int(os.environ.get("WEBUI_PORT", "8080")),
            webui_url=(os.environ.get("WEBUI_URL") or "").rstrip("/") or None,
        )
