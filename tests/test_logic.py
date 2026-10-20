from datetime import datetime, timedelta, timezone

from seerrcompanion.config import Config
from seerrcompanion.logic import Companion
from seerrcompanion.models import Request, parse_request
from seerrcompanion.store import Store

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def req(id, user=1, kind="tv", tmdb=None, status=2, media_status=5, seasons=(1,), day=0):
    return Request(
        id=id, status=status, kind=kind, tmdb_id=tmdb or id, media_id=id, media_status=media_status,
        rating_key=str(id), user_id=user, plex_id=user, user_name=f"u{user}", user_email=None,
        created=T0 + timedelta(days=day), seasons=tuple(sorted(seasons)),
    )


class FakeSeerr:
    def __init__(self, reqs):
        self.reqs, self.calls = reqs, []

    def requests(self): return list(self.reqs)
    def title(self, kind, tmdb): return f"Show{tmdb}"
    def set_seasons(self, i, s): self.calls.append(("seasons", i, s))
    def approve(self, i): self.calls.append(("approve", i))
    def delete_media_file(self, m): self.calls.append(("file", m))
    def delete_media(self, m): self.calls.append(("media", m))


class FakeWatch:
    def __init__(self, watched=()): self.w = set(watched)
    def watched(self, r): return r.id in self.w


class FakeNotifier:
    def __init__(self): self.sent = []
    def send(self, t, b): self.sent.append((t, b))


def build(reqs, watched=(), **cfg):
    c = Config(seerr_url="x", seerr_api_key="k", dry_run=False, **cfg)
    s, n = FakeSeerr(reqs), FakeNotifier()
    return Companion(c, s, FakeWatch(watched), n, Store(":memory:")), s, n


def test_trims_pending_multi_season_to_first_and_approves():
    app, s, _ = build([req(1, status=1, seasons=(3, 2, 4))])
    app.run_once()
    assert s.calls == [("seasons", 1, [2]), ("approve", 1)]


def test_trim_without_approve():
    app, s, _ = build([req(1, status=1, seasons=(1, 2))], approve_after_trim=False)
    app.run_once()
    assert s.calls == [("seasons", 1, [1])]


def test_single_season_and_movies_untouched():
    app, s, _ = build([req(1, status=1, seasons=(1,)), req(2, kind="movie", status=1, seasons=())])
    app.run_once()
    assert s.calls == []


def test_non_pending_multi_season_is_not_modified():
    app, s, _ = build([req(1, status=2, seasons=(1, 2))])
    app.run_once()
    assert s.calls == []


def test_dry_run_changes_nothing():
    c = Config(seerr_url="x", seerr_api_key="k", dry_run=True)
    s, n = FakeSeerr([req(1, status=1, seasons=(1, 2)), req(2, day=1)]), FakeNotifier()
    Companion(c, s, FakeWatch(), n, Store(":memory:")).run_once()
    assert s.calls == [] and n.sent == []


def test_first_run_baselines_history_then_flags_only_new():
    old = [req(1, day=0), req(2, day=1)]
    app, _, n = build(old)
    app.run_once()
    assert n.sent == []  # history not mass-flagged
    app.seerr.reqs.append(req(3, day=2))  # request 1 unwatched, 2 unwatched
    app.run_once()
    assert len(n.sent) == 1 and "Show2" in n.sent[0][1]
    assert [r["request_id"] for r in app.store.flagged()] == [2]


def test_watched_previous_not_flagged():
    app, _, n = build([req(1, day=0)], watched=[1])
    app.run_once()
    app.seerr.reqs.append(req(2, day=1))
    app.run_once()
    assert n.sent == []


def test_film_request_after_unwatched_series_flags_series():
    app, _, n = build([req(1, day=0)])
    app.run_once()
    app.seerr.reqs.append(req(2, kind="movie", day=1))
    app.run_once()
    assert len(n.sent) == 1


def test_unwatched_film_is_not_flagged():
    app, _, n = build([req(1, kind="movie", day=0)])
    app.run_once()
    app.seerr.reqs.append(req(2, day=1))
    app.run_once()
    assert n.sent == []


def test_same_series_new_season_is_not_another_series():
    app, _, n = build([req(1, tmdb=10, day=0)])
    app.run_once()
    app.seerr.reqs.append(req(2, tmdb=10, seasons=(2,), day=1))
    app.run_once()
    assert n.sent == []


def test_unavailable_previous_is_not_judged():
    app, _, n = build([req(1, media_status=3, day=0)])
    app.run_once()
    app.seerr.reqs.append(req(2, day=1))
    app.run_once()
    assert n.sent == []


def test_other_users_do_not_interact():
    app, _, n = build([req(1, user=1, day=0)])
    app.run_once()
    app.seerr.reqs.append(req(2, user=2, day=1))
    app.run_once()
    assert n.sent == []


def test_flag_cleared_when_watched_later_and_not_reflagged():
    app, _, n = build([req(1, day=0)])
    app.run_once()
    app.seerr.reqs.append(req(2, day=1))
    app.run_once()
    app.watch.w.add(1)
    app.run_once()
    assert app.store.flagged() == [] and n.sent[-1][0] == "Deletion cleared"


def test_purge_after_grace_period():
    app, s, n = build([req(1, day=0)], purge_after_days=7)
    app.run_once()
    app.seerr.reqs.append(req(2, day=1))
    app.run_once()
    app.store._db.execute("UPDATE flagged SET flagged_at = flagged_at - 8*86400")
    app.run_once()
    assert ("file", 1) in s.calls and ("media", 1) in s.calls
    assert n.sent[-1][0] == "Deleted"


def test_no_purge_by_default():
    app, s, _ = build([req(1, day=0)])
    app.run_once()
    app.seerr.reqs.append(req(2, day=1))
    app.run_once()
    app.store._db.execute("UPDATE flagged SET flagged_at = 0")
    app.run_once()
    assert s.calls == []


def test_parse_request_tolerates_real_shape():
    r = parse_request({
        "id": 5, "status": 1, "type": "tv", "createdAt": "2026-10-01T10:00:00.000Z",
        "media": {"id": 9, "tmdbId": 100, "status": 5, "ratingKey": 4242},
        "requestedBy": {"id": 3, "plexId": 777, "plexUsername": "bob"},
        "seasons": [{"seasonNumber": 2}, {"seasonNumber": 1}],
    })
    assert (r.kind, r.rating_key, r.plex_id, r.user_name, r.seasons) == ("tv", "4242", 777, "bob", (1, 2))


def test_dry_run_judges_each_pair_once(caplog):
    import logging
    c = Config(seerr_url="x", seerr_api_key="k", dry_run=True)
    s = FakeSeerr([req(1, day=0), req(2, day=1)])
    app = Companion(c, s, FakeWatch(), FakeNotifier(), Store(":memory:"))
    with caplog.at_level(logging.INFO):
        app.run_once()
        app.run_once()
    assert sum("would flag" in m for m in caplog.messages) == 1


def test_untrimmable_warns_once_as_summary(caplog):
    import logging
    app, _, _ = build([req(1, status=2, seasons=(1, 2)), req(2, status=2, seasons=(1, 3), tmdb=9)])
    with caplog.at_level(logging.WARNING):
        app.run_once()
        app.run_once()
    warns = [m for m in caplog.messages if "cannot be trimmed" in m]
    assert len(warns) == 1 and "1, 2" in warns[0]
