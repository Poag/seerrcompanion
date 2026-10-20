from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, urls: tuple[str, ...]):
        self._apprise = None
        if urls:
            import apprise

            self._apprise = apprise.Apprise()
            for u in urls:
                if not self._apprise.add(u):
                    log.error("Apprise rejected URL (check syntax): %s", u.split("://")[0] + "://...")

    def send(self, title: str, body: str) -> None:
        log.info("NOTIFY: %s - %s", title, body)
        if self._apprise is None:
            return
        try:
            if not self._apprise.notify(title=title, body=body):
                log.error("Apprise reported a delivery failure for %r", title)
        except Exception:  # never let a notification kill the loop
            log.exception("Apprise notify raised")
