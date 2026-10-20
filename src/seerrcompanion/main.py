from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from .config import Config
from .logic import Companion
from .notify import Notifier
from .seerr import SeerrClient
from .store import Store
from .tautulli import TautulliClient
from .watch import SeerrWatch, TautulliWatch

log = logging.getLogger("seerrcompanion")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    # Dry runs use a throwaway in-memory store so they never affect a later live run.
    os.makedirs(cfg.data_dir, exist_ok=True)
    store = Store(":memory:" if cfg.dry_run else os.path.join(cfg.data_dir, "seerrcompanion.db"))

    if len(sys.argv) > 1 and sys.argv[1] == "flagged":
        for row in Store(os.path.join(cfg.data_dir, "seerrcompanion.db")).flagged():
            print(f"{row['request_id']}\t{row['user_name']}\t{row['title']}")
        return

    seerr = SeerrClient(cfg.seerr_url, cfg.seerr_api_key)
    watch = TautulliWatch(TautulliClient(cfg.tautulli_url, cfg.tautulli_api_key)) if cfg.use_tautulli else SeerrWatch(seerr)
    log.info("Starting (dry_run=%s, watch source=%s)", cfg.dry_run, "tautulli" if cfg.use_tautulli else "seerr")
    app = Companion(cfg, seerr, watch, Notifier(cfg.apprise_urls), store)

    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            try:
                app.run_once()
            except Exception:
                log.exception("Poll failed; retrying next interval")
            stop.wait(cfg.poll_interval)

    if not cfg.webui_enabled:
        log.info("Web UI disabled (set WEBUI_USERNAME and WEBUI_PASSWORD to enable)")
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        poll()
        return

    import uvicorn

    from .web import create_app

    threading.Thread(target=poll, daemon=True, name="poller").start()
    try:
        uvicorn.run(create_app(app), host="0.0.0.0", port=cfg.webui_port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
