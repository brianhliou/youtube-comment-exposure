"""Panel loop: enroll fresh uploads, observe ranks per period, repeat-sample, close windows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from studies.comments import panel
from studies.comments.client import ResourceUnavailable
from studies.comments.quota import QuotaLedger, quota_day


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(UTC)
FRESH = _iso(NOW - timedelta(hours=3))
STALE = _iso(NOW - timedelta(days=4))
PERIOD = "2026-09-19a"
TODAY = quota_day()  # the ledger charges the real Pacific day, so the test must too


def thread_item(cid: str, likes: int) -> dict:
    return {
        "snippet": {
            "totalReplyCount": 1,
            "topLevelComment": {
                "id": cid,
                "snippet": {
                    "textDisplay": f"text {cid}",
                    "likeCount": likes,
                    "publishedAt": FRESH,
                    "authorChannelId": {"value": f"a-{cid}"},
                },
            },
        }
    }


def page(n: int, prefix: str = "c") -> dict:
    return {"items": [thread_item(f"{prefix}{i}", 100 - i) for i in range(n)]}


class FakeClient:
    """Routes by endpoint; `commentThreads` responses are keyed by video id.

    A video's entry may be a list, consumed one response per call, so a repeat fetch can
    return a different page than the first.
    """

    def __init__(self, uploads_per_channel: int = 1) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.threads: dict = {}
        self.uploads_per_channel = uploads_per_channel

    def get(self, endpoint: str, params: dict) -> dict:
        self.calls.append((endpoint, params))
        if endpoint == "channels":
            handle = params.get("forHandle")
            return {
                "items": [
                    {
                        "id": f"cid-{handle}",
                        "snippet": {"title": handle},
                        "contentDetails": {"relatedPlaylists": {"uploads": f"UU-{handle}"}},
                    }
                ]
            }
        if endpoint == "playlistItems":
            handle = params["playlistId"].removeprefix("UU-")
            fresh = [
                {
                    "contentDetails": {
                        "videoId": f"{handle}-new{i}",
                        "videoPublishedAt": _iso(NOW - timedelta(hours=3 + i)),
                    }
                }
                for i in range(self.uploads_per_channel)
            ]
            return {
                "items": fresh
                + [{"contentDetails": {"videoId": f"{handle}-old", "videoPublishedAt": STALE}}]
            }
        if endpoint == "videos":
            ids = params["id"].split(",")
            return {
                "items": [
                    {
                        "id": vid,
                        "snippet": {"title": f"t {vid}", "categoryId": "22"},
                        "contentDetails": {"duration": "PT5M"},
                        "statistics": {"viewCount": "100", "likeCount": "9", "commentCount": "40"},
                    }
                    for vid in ids
                    if not vid.endswith("gone")
                ]
            }
        if endpoint == "commentThreads":
            nxt = self.threads.get(params["videoId"], page(3))
            if isinstance(nxt, list):
                nxt = nxt.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        raise AssertionError(endpoint)


def _init(tmp_path, channels):
    db = tmp_path / "panel.sqlite"
    conn = panel.connect(db)
    for ch in channels:
        conn.execute(
            "INSERT INTO channels (handle, lang) VALUES (?, ?)", (ch["handle"], ch["lang"])
        )
    panel.set_meta(conn, "target", 10)
    panel.set_meta(conn, "window_days", 2)
    conn.commit()
    return conn


def _enroll(conn, client, ledger, **kw):
    opts = {"today": TODAY, "target": 10, "max_enroll": 5, "max_age_hours": 30}
    opts.update(kw)
    return panel._discover(conn, client, ledger, **opts)


def _insert_video(conn, video_id, enrolled_day=TODAY, handle="@en1"):
    conn.execute(
        "INSERT INTO panel_videos (video_id, channel_handle, lang, published_at, enrolled_day, "
        "enrolled_at) VALUES (?, ?, 'en', ?, ?, ?)",
        (video_id, handle, FRESH, enrolled_day, FRESH),
    )
    conn.commit()


def test_period_key_splits_the_pacific_day_at_noon():
    la = ZoneInfo("America/Los_Angeles")
    assert panel.period_now(datetime(2026, 9, 19, 1, 0, tzinfo=la)) == "2026-09-19a"
    assert panel.period_now(datetime(2026, 9, 19, 13, 0, tzinfo=la)) == "2026-09-19b"
    # 02:00 UTC on the 20th is still the 19th in Pacific, evening slot.
    assert panel.period_now(datetime(2026, 9, 20, 2, 0, tzinfo=UTC)) == "2026-09-19b"


def test_daily_pass_enrolls_fresh_only_and_records_ranks(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}, {"handle": "@zh1", "lang": "zh"}])
    client = FakeClient()
    client.threads = {
        "@en1-new0": {"items": [thread_item("c1", 5), thread_item("c2", 2)]},
        "@zh1-new0": {"items": [thread_item("d1", 1)]},
    }
    ledger = QuotaLedger(conn, budget=100)

    panel._resolve_channels(conn, client, ledger)
    assert _enroll(conn, client, ledger) == 2  # the fresh uploads; the 4-day-old ones skipped
    assert panel._observe_totals(conn, client, ledger, PERIOD) == 2
    assert panel._observe_ranks(conn, client, ledger, PERIOD) == 2

    rows = conn.execute(
        "SELECT video_id, period, rank, comment_id, like_count FROM rank_observations "
        "ORDER BY video_id, rank"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("@en1-new0", PERIOD, 1, "c1", 5),
        ("@en1-new0", PERIOD, 2, "c2", 2),
        ("@zh1-new0", PERIOD, 1, "d1", 1),
    ]
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM video_observations WHERE period = ?", (PERIOD,)
        ).fetchone()[0]
        == 2
    )
    # 2 channel resolves + 2 playlist pages + 1 videos.list + 2 rank pages
    assert ledger.used(TODAY) == 7
    assert (
        conn.execute("SELECT duration FROM panel_videos WHERE video_id = '@en1-new0'").fetchone()[0]
        == "PT5M"
    )
    # discovery recorded the channel's newest upload
    assert conn.execute("SELECT last_upload_at FROM channels WHERE handle = '@en1'").fetchone()[
        0
    ] == _iso(NOW - timedelta(hours=3))


def test_second_period_same_day_observes_again_without_re_enrolling(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}])
    client = FakeClient()
    ledger = QuotaLedger(conn, budget=100)
    panel._resolve_channels(conn, client, ledger)
    _enroll(conn, client, ledger)
    panel._observe_totals(conn, client, ledger, "2026-09-19a")
    panel._observe_ranks(conn, client, ledger, "2026-09-19a")
    spent = ledger.used(TODAY)

    # Same period again: nothing to do, no units.
    assert _enroll(conn, client, ledger) == 0
    assert panel._observe_totals(conn, client, ledger, "2026-09-19a") == 0
    assert panel._observe_ranks(conn, client, ledger, "2026-09-19a") == 0
    assert ledger.used(TODAY) == spent

    # Evening period: one more rank page and one more totals row per active video,
    # but discovery already ran today so no new playlist reads.
    assert _enroll(conn, client, ledger) == 0
    assert panel._observe_totals(conn, client, ledger, "2026-09-19b") == 1
    assert panel._observe_ranks(conn, client, ledger, "2026-09-19b") == 1
    assert ledger.used(TODAY) == spent + 2
    periods = {r[0] for r in conn.execute("SELECT DISTINCT period FROM rank_observations")}
    assert periods == {"2026-09-19a", "2026-09-19b"}


def test_reliability_repeat_is_stored_under_r_period(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}])
    _insert_video(conn, "v")
    client = FakeClient()
    client.threads = {"v": [page(12, "first"), page(12, "second")]}
    ledger = QuotaLedger(conn, budget=100)
    assert panel._observe_ranks(conn, client, ledger, PERIOD) == 1
    assert panel._reliability(conn, client, ledger, PERIOD, n=20) == 1
    # a second call in the same period does not repeat the repeat
    assert panel._reliability(conn, client, ledger, PERIOD, n=20) == 0
    first = [
        r[0]
        for r in conn.execute(
            "SELECT comment_id FROM rank_observations WHERE period = ? ORDER BY rank", (PERIOD,)
        )
    ]
    second = [
        r[0]
        for r in conn.execute(
            "SELECT comment_id FROM rank_observations WHERE period = ? ORDER BY rank",
            (PERIOD + "r",),
        )
    ]
    assert first[0] == "first0" and second[0] == "second0"
    assert (
        conn.execute(
            "SELECT outcome FROM rank_fetches WHERE period = ?", (PERIOD + "r",)
        ).fetchone()[0]
        == "repeat"
    )


def test_reliability_skips_tiny_pages(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}])
    _insert_video(conn, "v")
    client = FakeClient()
    client.threads = {"v": page(4)}
    ledger = QuotaLedger(conn, budget=100)
    panel._observe_ranks(conn, client, ledger, PERIOD)
    assert panel._reliability(conn, client, ledger, PERIOD, n=20) == 0


def test_window_closes_after_window_days(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}])
    _insert_video(conn, "v", enrolled_day="2026-09-10")
    # window_days=2 → observed on 09-10 and 09-11, closed from 09-12 on
    assert panel._close_windows(conn, "2026-09-11", 2) == 0
    assert panel._close_windows(conn, "2026-09-12", 2) == 1
    assert conn.execute("SELECT status FROM panel_videos").fetchone()[0] == panel.CLOSED


def test_comments_disabled_drops_video_but_keeps_fetch_row(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}])
    _insert_video(conn, "v")
    client = FakeClient()
    client.threads = {"v": ResourceUnavailable("commentsDisabled", "commentThreads")}
    ledger = QuotaLedger(conn, budget=100)
    assert panel._observe_ranks(conn, client, ledger, PERIOD) == 0
    status, reason = conn.execute("SELECT status, status_reason FROM panel_videos").fetchone()
    assert status == panel.DROPPED
    assert "commentsDisabled" in reason
    assert conn.execute("SELECT outcome FROM rank_fetches").fetchone()[0].startswith("unavailable")
    assert ledger.used(TODAY) == 0  # a 4xx is not billed


def test_deleted_video_dropped_by_totals(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}])
    _insert_video(conn, "v-gone")
    client = FakeClient()
    ledger = QuotaLedger(conn, budget=100)
    assert panel._observe_totals(conn, client, ledger, PERIOD) == 0
    assert conn.execute("SELECT status FROM panel_videos").fetchone()[0] == panel.DROPPED


def test_enrollment_caps_and_language_interleave(tmp_path):
    channels = [{"handle": f"@en{i}", "lang": "en"} for i in range(4)] + [
        {"handle": "@ja0", "lang": "ja"}
    ]
    conn = _init(tmp_path, channels)
    client = FakeClient()
    ledger = QuotaLedger(conn, budget=100)
    panel._resolve_channels(conn, client, ledger)
    assert _enroll(conn, client, ledger, max_enroll=2) == 2
    langs = {r[0] for r in conn.execute("SELECT lang FROM panel_videos")}
    assert langs == {"en", "ja"}  # round-robin gave the lone ja channel a slot


def test_per_channel_caps_per_day_and_total(tmp_path):
    conn = _init(tmp_path, [{"handle": "@en1", "lang": "en"}])
    client = FakeClient(uploads_per_channel=4)  # a heavy poster: 4 fresh uploads today
    ledger = QuotaLedger(conn, budget=100)
    panel._resolve_channels(conn, client, ledger)
    assert _enroll(conn, client, ledger, max_enroll=10) == panel.MAX_PER_CHANNEL_PER_DAY
    # Pre-load the channel to the total cap; the next day's discovery skips it entirely.
    for i in range(panel.MAX_PER_CHANNEL_TOTAL - panel.MAX_PER_CHANNEL_PER_DAY):
        _insert_video(conn, f"pre{i}", enrolled_day="2026-09-18")
    calls_before = len(client.calls)
    assert _enroll(conn, client, ledger, today="2026-09-20", max_enroll=10) == 0
    assert len(client.calls) == calls_before  # not even the playlist page was spent


def test_per_language_cap_equalises_the_panel(tmp_path):
    # ja posts every day, en never: without the cap ja would take the whole panel.
    conn = _init(
        tmp_path,
        [
            {"handle": "@ja0", "lang": "ja"},
            {"handle": "@ja1", "lang": "ja"},
            {"handle": "@ja2", "lang": "ja"},
            {"handle": "@en0", "lang": "en"},
        ],
    )
    client = FakeClient(uploads_per_channel=2)
    ledger = QuotaLedger(conn, budget=100)
    panel._resolve_channels(conn, client, ledger)
    # target 10 over 2 languages → 5 per language; ja offers 6 fresh videos today
    assert _enroll(conn, client, ledger, target=10, max_enroll=80) == 5 + 2
    by_lang = dict(conn.execute("SELECT lang, COUNT(*) FROM panel_videos GROUP BY lang"))
    assert by_lang == {"ja": 5, "en": 2}
