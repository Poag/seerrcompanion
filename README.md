# seerrcompanion

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
| `IGNORE_USER_IDS` | unset | Comma-separated Seerr user ids to skip |
| `DATA_DIR` | `/config` | Holds `seerrcompanion.db` |

```
docker run -d --name seerrcompanion -v /mnt/user/appdata/seerrcompanion:/config \
  -e SEERR_URL=http://seerr:5055 -e SEERR_API_KEY=... \
  -e TAUTULLI_URL=http://tautulli:8181 -e TAUTULLI_API_KEY=... \
  -e APPRISE_URLS='discord://...' ghcr.io/poag/seerrcompanion:latest
```

`docker exec seerrcompanion seerrcompanion flagged` lists what is currently marked for deletion.

## Behaviour you need to know

- **Season trimming needs requests to be pending.** Seerr only lets you edit a pending request (409 otherwise), and once a request is approved Sonarr has already grabbed every season. So users must not have auto-approve; the companion trims then approves (`APPROVE_AFTER_TRIM`). Already-processed multi-season requests are logged once and left alone. Season 0 (specials) is dropped when other seasons are requested.
- **"Marked for deletion" is state in the companion's own database plus a notification.** Seerr has no tag or flag for this. Flags clear automatically (with a notification) if the user watches the title later.
- **Only series are flagged**, judged against the user's immediately preceding request (series or film). Another season of the same series does not count as another title.
- **A title is only judged once it is available** in Seerr. If a user requests something new before the previous series finished downloading, the previous one is not judged.
- **Never flags on uncertainty.** If the Plex ratingKey or the Tautulli user cannot be found, the request is skipped with a warning.
- **First live run baselines** all existing requests as seen, so history is not mass-flagged. `DRY_RUN` uses an in-memory store and no baseline, so it previews against your whole history.
- With Tautulli, "watched" means any play of the title (any episode of a show) by that user after the request was created. Seerr's fallback has no timestamps and means "ever watched by this user".
