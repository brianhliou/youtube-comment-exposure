"""Corpus store: idempotent writes, resume checkpoints, and population binding."""

from __future__ import annotations

import pytest

from studies.comments import store

VIDEOS = [
    {"video_id": "v_big", "channel_id": "c1", "comment_count": 900},
    {"video_id": "v_small", "channel_id": "c1", "comment_count": 300},
]


def comment(cid: str, likes: int = 1, published: str = "2026-01-01T01:00:00Z") -> dict:
    return {
        "comment_id": cid,
        "author_channel_id": "a1",
        "text": "nice",
        "like_count": likes,
        "reply_count": 0,
        "published_at": published,
        "updated_at": None,
    }


def test_store_comments_is_idempotent_and_counts_only_new_rows(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, VIDEOS)

    assert store.store_comments(conn, "v_big", [comment("x"), comment("y")], "t0") == 2
    # Replaying the same page adds nothing, so a resumed page cannot double-count.
    assert store.store_comments(conn, "v_big", [comment("x"), comment("y")], "t1") == 0
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 2


def test_replaying_a_page_refreshes_the_observation(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, VIDEOS)
    store.store_comments(conn, "v_big", [comment("x", likes=5)], "t0")
    store.store_comments(conn, "v_big", [comment("x", likes=9)], "t1")

    row = conn.execute(
        "SELECT like_count, fetched_at FROM comments WHERE comment_id='x'"
    ).fetchone()
    assert row["like_count"] == 9
    assert row["fetched_at"] == "t1"


def test_next_video_resumes_in_progress_work_first(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, VIDEOS)

    # Smallest pending video leads while nothing is in flight.
    assert store.next_video(conn)["video_id"] == "v_small"

    store.checkpoint_page(conn, "v_big", "token-1", 100)
    assert store.next_video(conn)["video_id"] == "v_big"


def test_checkpoint_records_the_resume_token_and_progress(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, VIDEOS)
    store.checkpoint_page(conn, "v_big", "token-1", 100)
    store.checkpoint_page(conn, "v_big", "token-2", 80)

    row = conn.execute("SELECT * FROM videos WHERE video_id='v_big'").fetchone()
    assert row["status"] == store.IN_PROGRESS
    assert row["page_token"] == "token-2"
    assert row["pages_fetched"] == 2
    assert row["comments_stored"] == 180


def test_finished_videos_leave_the_queue_and_clear_their_token(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, VIDEOS)
    store.checkpoint_page(conn, "v_small", "token-1", 10)
    store.finish_video(conn, "v_small", store.DONE)
    store.finish_video(conn, "v_big", store.SKIPPED, "commentsDisabled")

    assert store.next_video(conn) is None
    token = conn.execute("SELECT page_token FROM videos WHERE video_id='v_small'").fetchone()[0]
    assert token is None


def test_upsert_videos_preserves_existing_collection_state(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, VIDEOS)
    store.checkpoint_page(conn, "v_big", "token-1", 42)
    store.upsert_videos(conn, VIDEOS)  # re-running `run` re-applies the manifest

    row = conn.execute("SELECT * FROM videos WHERE video_id='v_big'").fetchone()
    assert row["page_token"] == "token-1"
    assert row["comments_stored"] == 42


def test_database_refuses_a_second_population(tmp_path):
    """Swapping the sample midway would redefine the experiment with no record."""
    conn = store.connect(tmp_path / "c.sqlite")
    store.bind_population(conn, "hash-a")
    store.bind_population(conn, "hash-a")  # same population re-binds cleanly
    with pytest.raises(ValueError, match="refusing to collect"):
        store.bind_population(conn, "hash-b")


def test_relevance_ranks_record_displayed_order(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, VIDEOS)
    store.store_relevance_ranks(conn, "v_big", ["c", "a", "b"], "t0")
    store.store_relevance_ranks(conn, "v_big", ["a", "c", "b"], "t1")  # re-probe

    ranks = dict(conn.execute("SELECT comment_id, rank FROM relevance_ranks").fetchall())
    assert ranks == {"a": 1, "c": 2, "b": 3}
