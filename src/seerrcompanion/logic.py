from __future__ import annotations

import logging
import time
from collections import defaultdict

from .config import Config
from .models import APPROVED, DECLINED, PENDING, Request
from .notify import Notifier
from .seerr import SeerrClient
from .store import Store
from .watch import WatchSource

log = logging.getLogger(__name__)


class Companion:
    def __init__(self, cfg: Config, seerr: SeerrClient, watch: WatchSource, notifier: Notifier, store: Store):
        self.cfg, self.seerr, self.watch, self.notifier, self.store = cfg, seerr, watch, notifier, store
        self._baselined = False

    def run_once(self) -> None:
        reqs = [r for r in self.seerr.requests() if r.user_id not in self.cfg.ignore_user_ids]
        if self.cfg.trim_seasons:
            for r in reqs:
                self.trim(r)
        if self.cfg.flag_unwatched:
            self.baseline(reqs)
            self.detect(reqs)
            self.review_flagged(reqs)

    # Feature 2: multi-season request -> first requested season only

    def trim(self, r: Request) -> None:
        if r.kind != "tv":
            return
        # Season 0 is "specials"; only count it if it is all that was asked for.
        wanted = [s for s in r.seasons if s > 0] or list(r.seasons)
        if len(wanted) <= 1 and len(r.seasons) <= 1:
            return
        first = wanted[0]
        if r.status != PENDING:
            # Seerr rejects edits to non-pending requests, and the *arr has usually already
            # grabbed everything by then. Say so once rather than every poll.
            if self.store.warn_untrimmable_once(r.id):
                log.warning(
                    "Request %s (%s) asked for seasons %s but is already processed and cannot be "
                    "trimmed. Remove auto-approve for this user so requests stay pending.",
                    r.id, r.user_name, list(r.seasons),
                )
            return
        if self.cfg.dry_run:
            log.info("[DRY] would trim request %s (%s) from seasons %s to [%s]", r.id, r.user_name, list(r.seasons), first)
            return
        self.seerr.set_seasons(r.id, [first])
        log.info("Trimmed request %s (%s) from seasons %s to [%s]", r.id, r.user_name, list(r.seasons), first)
        if self.cfg.approve_after_trim:
            self.seerr.approve(r.id)
            log.info("Approved request %s", r.id)

    # Feature 1: previous series unwatched when the next request arrives

    def baseline(self, reqs: list[Request]) -> None:
        """First live run: treat existing history as already seen so it is not mass-flagged."""
        if self._baselined or self.cfg.dry_run:
            return
        self._baselined = True
        if self.store.is_empty():
            self.store.mark_seen(*(r.id for r in reqs))
            log.info("First run: baselined %d existing requests", len(reqs))

    def detect(self, reqs: list[Request]) -> None:
        seen = self.store.seen()
        by_user: dict[int, list[Request]] = defaultdict(list)
        for r in reqs:
            if r.status != DECLINED:  # ignore declined requests entirely
                by_user[r.user_id].append(r)
        new_ids: list[int] = []
        for user_reqs in by_user.values():
            user_reqs.sort(key=lambda r: r.created)
            for prev, new in zip(user_reqs, user_reqs[1:]):
                if new.id in seen:
                    continue
                new_ids.append(new.id)
                self.consider(prev, new)
            if user_reqs and user_reqs[0].id not in seen:
                new_ids.append(user_reqs[0].id)
        if not self.cfg.dry_run:
            self.store.mark_seen(*new_ids)

    def consider(self, prev: Request, new: Request) -> None:
        if prev.kind != "tv" or prev.tmdb_id == new.tmdb_id:
            return  # only series are flagged; a further season of the same series is not "another" title
        if prev.status != APPROVED or not prev.available:
            log.debug("Request %s not yet available; cannot judge it", prev.id)
            return
        if self.store.is_flagged(prev.id):
            return
        if self.watch.watched(prev) is not False:
            return  # watched, or undeterminable (never flag on uncertainty)
        title = self.seerr.title(prev.kind, prev.tmdb_id)
        if self.cfg.dry_run:
            log.info("[DRY] would flag %r (request %s, %s): not watched before request %s", title, prev.id, prev.user_name, new.id)
            return
        self.store.flag(prev.id, prev.media_id, prev.user_id, prev.user_name, title, new.id)
        self.notifier.send(
            "Marked for deletion",
            f"{prev.user_name} requested '{title}' but did not watch it before requesting another title. "
            "It is marked for deletion.",
        )

    def review_flagged(self, reqs: list[Request]) -> None:
        """Clear flags for titles watched since, and purge old flags if configured."""
        if self.cfg.dry_run:
            return
        by_id = {r.id: r for r in reqs}
        for row in self.store.flagged():
            r = by_id.get(row["request_id"])
            if r is None:  # request removed in Seerr
                self.store.set_state(row["request_id"], "cleared")
                continue
            if self.watch.watched(r):
                self.store.set_state(r.id, "cleared")
                self.notifier.send("Deletion cleared", f"{row['user_name']} has now watched '{row['title']}'; no longer marked for deletion.")
                continue
            days = self.cfg.purge_after_days
            if days is not None and time.time() - row["flagged_at"] >= days * 86400:
                self.seerr.delete_media_file(r.media_id)
                self.seerr.delete_media(r.media_id)
                self.store.set_state(r.id, "purged")
                self.notifier.send("Deleted", f"'{row['title']}' (requested by {row['user_name']}) was unwatched for {days} days and has been deleted.")
