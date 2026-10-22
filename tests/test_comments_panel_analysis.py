"""Panel analysis against a synthetic panel with known dynamics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from studies.comments import panel
from studies.comments import panel_analysis as pa

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(conn, video_id, lang, channel, page_at, *, duration="PT10M", count=1000, marquee=0):
    """page_at: {age_hours: [(comment_id, likes, posted_age_h), ...]} in rank order.

    Ages ending in .5 land in a 'b' period so two observations can share a day.
    """
    conn.execute(
        "INSERT OR IGNORE INTO channels (handle, lang, marquee, channel_id) VALUES (?, ?, ?, ?)",
        (channel, lang, marquee, f"cid-{channel}"),
    )
    conn.execute(
        "INSERT INTO panel_videos (video_id, channel_handle, channel_id, lang, published_at, "
        "enrolled_day, enrolled_at, duration) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            video_id,
            channel,
            f"cid-{channel}",
            lang,
            iso(T0),
            T0.date().isoformat(),
            iso(T0),
            duration,
        ),
    )
    for age, ranked in page_at.items():
        fetched = T0 + timedelta(hours=age)
        period = fetched.date().isoformat() + ("a" if fetched.hour < 12 else "b")
        conn.execute(
            "INSERT OR REPLACE INTO video_observations VALUES (?, ?, ?, 1000, 50, ?)",
            (video_id, period, iso(fetched), count),
        )
        for rank, (cid, likes, posted_age) in enumerate(ranked, start=1):
            conn.execute(
                "INSERT OR REPLACE INTO rank_observations "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, 'x', ?)",
                (
                    video_id,
                    period,
                    rank,
                    cid,
                    f"a-{cid}",
                    likes,
                    iso(T0 + timedelta(hours=posted_age)),
                    iso(fetched),
                ),
            )
        conn.execute(
            "INSERT OR REPLACE INTO rank_fetches VALUES (?, ?, ?, ?, 'ok')",
            (video_id, period, iso(fetched), len(ranked)),
        )
    conn.commit()


def _page(ids, likes=10, posted=0.5):
    return [(c, likes, posted) for c in ids]


def test_lockin_entry_age_and_floor(tmp_path):
    conn = panel.connect(tmp_path / "p.sqlite")
    final = [f"c{i}" for i in range(12)]
    # Video A: page settles by hour 30 (top-10 = final from then on); early pages differ.
    a_pages = {
        6: _page([f"e{i}" for i in range(6)] + final[:6]),  # 6/10 overlap → J = 6/14
        18: _page(final[:8] + ["e0", "e1"] + final[8:]),  # top-10 has 8 of final → J 8/12
        30: _page(final),  # locked
        54: _page(final),
        102: _page(final),
        170: _page(final),  # endpoint at 168h
    }
    _seed(conn, "A", "en", "@en1", a_pages)
    # Video B: locked from its first observation (hour 4).
    _seed(conn, "B", "ja", "@ja1", {4: _page(final), 100: _page(final), 175: _page(final)})
    # Video C: too small — excluded by the 100-top-level rule.
    _seed(conn, "C", "en", "@en2", {4: _page(final), 175: _page(final)}, count=50)
    # Video D: no endpoint yet.
    _seed(conn, "D", "zh", "@zh1", {4: _page(final), 40: _page(final)})
    # Repeat fetch for A at hour 30 that swaps two comments in the top 10 → J10 = 8/12.
    rep = _page(final[:8] + ["r1", "r2"] + final[8:])
    fetched = T0 + timedelta(hours=30)
    period = fetched.date().isoformat() + "b" + "r"
    for rank, (cid, likes, _posted) in enumerate(rep, start=1):
        conn.execute(
            "INSERT INTO rank_observations VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, 'x', ?)",
            ("A", period, rank, cid, f"a-{cid}", likes, iso(T0), iso(fetched)),
        )
    conn.execute(
        "INSERT INTO rank_fetches VALUES ('A', ?, ?, 12, 'repeat')", (period, iso(fetched))
    )
    conn.commit()

    res = pa.analyse(conn, hours=168)
    lk = res["lockin"]
    assert lk["excluded"] == {"marquee": 0, "no_endpoint_yet": 1, "under_min_comments": 1}
    rows = {r["video_id"]: r for r in lk["per_video"]}
    assert rows["A"]["lockin_first_h"] == 30
    assert rows["A"]["lockin_sustained_h"] == 30
    assert rows["B"]["lockin_first_h"] == 4
    assert lk["pooled"]["already_locked_at_first_obs"] == 1
    assert lk["pooled"]["first_crossing_h"]["median"] == 17  # median of {4, 30}
    assert lk["by_lang"]["en"]["first_crossing_h"]["median"] == 30
    # entry age: all endpoint top-20 comments were posted at 0.5 h
    assert res["entry_age"]["pooled"]["within_1h"] == 1.0
    # reliability floor from the one repeat pair: |{8 shared}| / |12| = 0.667
    assert res["reliability"]["pairs"] == 1
    assert abs(res["reliability"]["top10_jaccard"]["median"] - 8 / 12) < 1e-9
    assert res["reliability"]["threshold_clear"] is False
    # persistence 24-48h bin: A's hour-30 (J=1); D's hour-40 excluded (no endpoint yet)
    assert res["persistence"]["24-48h"]["n"] == 1
    assert res["hypotheses"]["gate_200_videos_with_endpoint"] is False


def test_feedback_loop_within_comment(tmp_path):
    conn = panel.connect(tmp_path / "p.sqlite")

    # One comment 'm' sits at rank 60 for hours 10→30 (+2 likes per 10 h → 0.2/h), then at
    # rank 1 for hours 30→40 (+20 → 2.0/h). Gain is attributed to the rank at the *start*
    # of each interval. Filler comments hold the other ranks.
    def page(m_rank, m_likes):
        ids = [f"f{i}" for i in range(70)]
        ids.insert(m_rank - 1, "m")
        return [(c, m_likes if c == "m" else 5, 0.5) for c in ids]

    _seed(
        conn,
        "V",
        "en",
        "@en1",
        {10: page(60, 0), 20: page(60, 2), 30: page(1, 4), 40: page(1, 24)},
    )
    res = pa.analyse(conn, hours=168)
    fb = res["feedback"]["within_comment"]
    assert fb["movers"] == 1
    assert abs(fb["mean_gain_per_h_at_top10"] - 2.0) < 1e-9
    assert abs(fb["mean_gain_per_h_at_51plus"] - 0.2) < 1e-9
    assert abs(fb["ratio"] - 10.0) < 1e-9
    assert res["hypotheses"]["H3_within_comment_ratio_ge_3"] is True


def test_marquee_and_creator_comments_are_excluded(tmp_path):
    conn = panel.connect(tmp_path / "p.sqlite")
    final = [f"c{i}" for i in range(12)]
    _seed(conn, "M", "en", "@MrBeast", {4: _page(final), 175: _page(final)}, marquee=1)
    # Creator comment at rank 1 of an ordinary video: dropped and the page re-ranked.
    _seed(conn, "N", "en", "@en1", {4: _page(final), 175: _page(final)})
    conn.execute(
        "UPDATE rank_observations SET author_channel_id = 'cid-@en1' "
        "WHERE video_id = 'N' AND rank = 1"
    )
    conn.commit()
    res = pa.analyse(conn, hours=168)
    assert res["lockin"]["excluded"]["marquee"] == 1
    assert [r["video_id"] for r in res["lockin"]["per_video"]] == ["N"]
    assert "@MrBeast" not in res["lockin"]["per_channel"]
    # 11 non-creator comments remain; top-10 excludes c0 (the creator) → still locked
    assert res["lockin"]["per_video"][0]["lockin_first_h"] == 4


def test_duration_parse():
    assert pa.parse_duration("PT1M6S") == 66
    assert pa.parse_duration("PT2H49M36S") == 10176
    assert pa.parse_duration("P1DT2H") == 93600
    assert pa.parse_duration(None) is None
