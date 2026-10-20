# seerrcompanion

<img src="assets/icon.png" width="96" alt="seerrcompanion icon">

A small companion service for [Seerr](https://github.com/seerr-team/seerr) that watches what users request and what they actually watch.

1. **Unwatched requests are marked for deletion.** If a user requests a series, does not watch it, and then requests another title (series or film), the earlier series is marked for deletion and a notification is sent through [Apprise](https://github.com/caronc/apprise).
2. **Multi-season requests are trimmed to the first season.** If a user requests several seasons, the request is changed to the lowest requested season. [buffarr](https://github.com/Poag/buffarr) fills in the rest as they watch.

## Setup

| Variable | Default | Purpose |
|---|---|---|
| `SEERR_URL`, `SEERR_API_KEY` | required | Seerr base URL and an admin API key |
| `TAUTULLI_URL`, `TAUTULLI_API_KEY` | unset | Watch state from Tautulli. If unset, Seerr's own `/media/{id}/watch_data` is used instead |
| `APPRISE_URLS` | unset | Comma-separated Apprise URLs. Unset means log only |
| `DRY_RUN` | `true` | Log what would happen; change and notify nothing. Set `false` to go live |
| `POLL_INTERVAL` | `60` | Seconds between polls |
| `TRIM_SEASONS` | `true` | Feature 2 |
| `APPROVE_AFTER_TRIM` | `true` | Approve a request once it has been trimmed |
| `FLAG_UNWATCHED` | `true` | Feature 1 |
| `PURGE_AFTER_DAYS` | unset | Delete flagged media (Seerr's file + media delete) after this many days still unwatched. **Off by default; nothing is ever deleted unless you set it** |
| `WEBUI_USERNAME`, `WEBUI_PASSWORD` | unset | Enable the web UI (port `WEBUI_PORT`, default 8080). Unset means no UI |
| `WEBUI_URL` | unset | Public URL of the UI; added to notifications as a review link |
| `SEERR_PUBLIC_URL` | `SEERR_URL` | Base URL for title links in the UI, if `SEERR_URL` is an internal address |
| `WEBUI_SECRET` | generated | Session signing key; otherwise generated into `/config/webui.secret` |
| `IGNORE_USER_IDS` | unset | Comma-separated Seerr user ids to skip |
| `DATA_DIR` | `/config` | Holds `seerrcompanion.db` |

```
docker run -d --name seerrcompanion -v /mnt/user/appdata/seerrcompanion:/config \
  -e SEERR_URL=http://seerr:5055 -e SEERR_API_KEY=... \
  -e TAUTULLI_URL=http://tautulli:8181 -e TAUTULLI_API_KEY=... \
  -e APPRISE_URLS='discord://...' -e WEBUI_USERNAME=admin -e WEBUI_PASSWORD=... -p 8080:8080 ghcr.io/poag/seerrcompanion:latest
```

`docker exec seerrcompanion seerrcompanion flagged` lists what is currently marked for deletion.

## Web UI

With `WEBUI_USERNAME` and `WEBUI_PASSWORD` set, the container serves a review page on port 8080 listing everything marked for deletion, with **Keep** and **Delete** per title (Delete asks for confirmation and removes the files and the media entry through Seerr). Kept titles are never flagged again. A history table shows past outcomes. Sessions last 7 days in a `SameSite=Strict` cookie, forms carry a CSRF token, and five failed logins from one address locks that address out for five minutes. It serves plain HTTP, so put it behind HTTPS (reverse proxy or Tailscale) before exposing it beyond your LAN. Behind a proxy all clients share the proxy's address, so the lockout applies to everyone at once.

## Behaviour you need to know

- **Season trimming needs requests to be pending.** Seerr only lets you edit a pending request (409 otherwise), and once a request is approved Sonarr has already grabbed every season. So users must not have auto-approve; the companion trims then approves (`APPROVE_AFTER_TRIM`). Already-processed multi-season requests are logged once and left alone. Season 0 (specials) is dropped when other seasons are requested.
- **"Marked for deletion" is state in the companion's own database plus a notification.** Seerr has no tag or flag for this. Flags clear automatically (with a notification) if the user watches the title later.
- **Only series are flagged**, judged against the user's immediately preceding request (series or film). Another season of the same series does not count as another title.
- **A title is only judged once it is available** in Seerr. If a user requests something new before the previous series finished downloading, the previous one is not judged.
- **Never flags on uncertainty.** If the Plex ratingKey or the Tautulli user cannot be found, the request is skipped with a warning.
- **First live run baselines** all existing requests as seen, so history is not mass-flagged. `DRY_RUN` uses an in-memory store and no baseline, so it previews against your whole history.
- With Tautulli, "watched" means any play of the title (any episode of a show) by that user after the request was created. Seerr's fallback has no timestamps and means "ever watched by this user".
