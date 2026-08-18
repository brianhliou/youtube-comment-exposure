"""Collection loop: quota charged on success only, resume, and terminal outcomes."""

from __future__ import annotations

from studies.comments import store
from studies.comments.client import QuotaExceeded, ResourceUnavailable, TransientError
from studies.comments.collect import collect_video, parse_thread
from studies.comments.quota import QuotaLedger

VIDEO = {"video_id": "v1", "channel_id": "c1", "comment_count": 300}


def thread_item(cid: str, likes: int = 3) -> dict:
    return {
        "snippet": {
            "totalReplyCount": 2,
            "topLevelComment": {
                "id": cid,
                "snippet": {
                    "textDisplay": "hi",
                    "likeCount": likes,
                    "publishedAt": "2026-01-01T01:00:00Z",
                    "authorChannelId": {"value": "a1"},
                },
            },
        }
    }


def threads_body(ids: list[str], next_token: str | None = None) -> dict:
    body: dict = {"items": [thread_item(i) for i in ids]}
    if next_token:
        body["nextPageToken"] = next_token
    return body


class FakeClient:
    """Serves canned responses in order; raises any response that is an exception."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.units_spent = 0

    def get(self, endpoint: str, params: dict):
        self.calls.append((endpoint, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        self.units_spent += 1
        return response


def setup_db(tmp_path, videos=(VIDEO,)):
    conn = store.connect(tmp_path / "c.sqlite")
    store.upsert_videos(conn, list(videos))
    return conn


def test_full_enumeration_pages_then_probes_relevance(tmp_path):
    conn = setup_db(tmp_path)
    client = FakeClient(
        [
            threads_body(["a", "b"], "t2"),
            threads_body(["c"]),
            threads_body(["b", "a"]),  # the relevance probe
        ]
    )
    ledger = QuotaLedger(conn, budget=10)

    outcome, added = collect_video(conn, client, ledger, dict(VIDEO, page_token=None))

    assert outcome == "done"
    assert added == 3
    assert ledger.used() == 3  # two pages + one probe
    assert conn.execute("SELECT status FROM videos WHERE video_id='v1'").fetchone()[0] == store.DONE

    ranks = dict(conn.execute("SELECT comment_id, rank FROM relevance_ranks").fetchall())
    assert ranks == {"b": 1, "a": 2}
    assert client.calls[0][1]["order"] == "time"
    assert client.calls[-1][1]["order"] == "relevance"


def test_resume_starts_from_the_stored_page_token(tmp_path):
    conn = setup_db(tmp_path)
    client = FakeClient([threads_body(["c"]), threads_body(["c"])])
    ledger = QuotaLedger(conn, budget=10)

    collect_video(conn, client, ledger, dict(VIDEO, page_token="tok-mid"))

    assert client.calls[0][1]["pageToken"] == "tok-mid"


def test_running_out_of_quota_pauses_without_losing_the_resume_point(tmp_path):
    conn = setup_db(tmp_path)
    client = FakeClient([threads_body(["a"], "t2")])
    ledger = QuotaLedger(conn, budget=1)

    outcome, added = collect_video(conn, client, ledger, dict(VIDEO, page_token=None))

    assert outcome == "paused"
    assert added == 1
    row = conn.execute("SELECT status, page_token FROM videos WHERE video_id='v1'").fetchone()
    assert row["status"] == store.IN_PROGRESS
    assert row["page_token"] == "t2"


def test_unavailable_resource_is_skipped_and_costs_no_quota(tmp_path):
    conn = setup_db(tmp_path)
    client = FakeClient([ResourceUnavailable("commentsDisabled", "commentThreads")])
    ledger = QuotaLedger(conn, budget=10)

    outcome, _ = collect_video(conn, client, ledger, dict(VIDEO, page_token=None))

    assert outcome == "skipped"
    assert ledger.used() == 0
    row = conn.execute("SELECT status, status_reason FROM videos WHERE video_id='v1'").fetchone()
    assert row["status"] == store.SKIPPED
    assert row["status_reason"] == "commentsDisabled"


def test_quota_error_exhausts_the_day(tmp_path):
    conn = setup_db(tmp_path)
    client = FakeClient([QuotaExceeded("commentThreads: quotaExceeded")])
    ledger = QuotaLedger(conn, budget=10)

    outcome, _ = collect_video(conn, client, ledger, dict(VIDEO, page_token=None))

    assert outcome == "quota"
    assert ledger.remaining() == 0
    # The video is untouched, so tomorrow's run resumes it rather than skipping it.
    status = conn.execute("SELECT status FROM videos WHERE video_id='v1'").fetchone()[0]
    assert status == store.PENDING


def test_exhausted_retries_mark_the_video_failed_not_done(tmp_path):
    conn = setup_db(tmp_path)
    client = FakeClient([TransientError("commentThreads failed after 5 attempts")])
    ledger = QuotaLedger(conn, budget=10)

    outcome, _ = collect_video(conn, client, ledger, dict(VIDEO, page_token=None))

    assert outcome == "failed"
    status = conn.execute("SELECT status FROM videos WHERE video_id='v1'").fetchone()[0]
    assert status == store.FAILED


def test_parse_thread_extracts_the_analysis_fields():
    row = parse_thread(thread_item("abc", likes=7))
    assert row == {
        "comment_id": "abc",
        "author_channel_id": "a1",
        "text": "hi",
        "like_count": 7,
        "reply_count": 2,
        "published_at": "2026-01-01T01:00:00Z",
        "updated_at": None,
    }


def test_parse_thread_rejects_rows_missing_the_exposure_fields():
    assert parse_thread({"snippet": {}}) is None

    no_timestamp = thread_item("abc")
    del no_timestamp["snippet"]["topLevelComment"]["snippet"]["publishedAt"]
    assert parse_thread(no_timestamp) is None


def test_parse_thread_falls_back_to_text_original():
    item = thread_item("abc")
    snippet = item["snippet"]["topLevelComment"]["snippet"]
    del snippet["textDisplay"]
    snippet["textOriginal"] = "original body"
    assert parse_thread(item)["text"] == "original body"
