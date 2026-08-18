"""Feature construction and the held-out-channel split.

Two rules govern what may become a feature, and both exist to stop the baseline from
cheating:

- **Only quantities knowable when the comment was posted.** Displayed rank is excluded
  outright: it is *caused* by like count, so a model given it would be predicting likes
  from likes and would post a spectacular, meaningless score.
- **Splits are by channel.** Splitting by comment leaks the video and its audience into
  training; splitting by video still leaks the channel's comment culture. Only a
  channel-level split answers "does this transfer to an audience we never saw?"

The target is a comment's **within-video percentile rank of like count**, which removes
the video fixed effect from the training signal rather than asking a model to learn it.
Video-level features are retained anyway so the ladder can show they contribute nothing
to *within-video* ordering.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

import numpy as np

# Features available at post time. Displayed rank is deliberately absent.
FEATURE_NAMES = [
    "age_pct",  # position in the video's comment timeline, 0 = earliest
    "log_age_h",  # hours between video publish and comment publish
    "log_views",
    "log_video_comments",
    "is_short",
]
LENGTH_FEATURES = ["log_chars", "log_words", "has_question", "has_caps_run"]


@dataclass
class Dataset:
    x: np.ndarray  # exposure features
    x_len: np.ndarray  # length/shape features
    text: list[str]
    y: np.ndarray  # within-video percentile rank of like_count, in [0, 1]
    likes: np.ndarray
    video_idx: np.ndarray  # integer video id per row
    channel: np.ndarray  # channel handle per row
    # video_idx -> the real YouTube video id, so callers can join back to video
    # metadata without re-deriving the enumeration order and getting it wrong.
    video_ids: dict[int, str] = field(default_factory=dict)

    def subset(self, mask: np.ndarray) -> Dataset:
        return Dataset(
            self.x[mask], self.x_len[mask], [t for t, m in zip(self.text, mask, strict=True) if m],
            self.y[mask], self.likes[mask], self.video_idx[mask], self.channel[mask],
            self.video_ids,
        )


def _length_features(text: str) -> list[float]:
    chars, words = len(text), len(text.split())
    caps_run = 1.0 if any(len(w) > 3 and w.isupper() for w in text.split()) else 0.0
    return [math.log1p(chars), math.log1p(words), 1.0 if "?" in text else 0.0, caps_run]


def load(db_path: str, min_comments_per_video: int = 20) -> Dataset:
    """Read the corpus and build per-comment features + within-video rank targets."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT cm.video_id, cm.text, cm.like_count,
               (julianday(cm.published_at) - julianday(v.published_at)) * 24.0 AS age_h,
               v.view_count, v.comment_count, v.duration, v.channel_handle
        FROM comments cm JOIN videos v USING(video_id)
        WHERE v.status = 'done' AND cm.published_at IS NOT NULL
        ORDER BY cm.video_id, cm.published_at
        """
    ).fetchall()
    conn.close()

    by_video: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_video.setdefault(r["video_id"], []).append(r)

    x, x_len, text, y, likes, video_idx, channel = [], [], [], [], [], [], []
    video_ids: dict[int, str] = {}
    for vi, (_vid, group) in enumerate(by_video.items()):
        n = len(group)
        if n < min_comments_per_video:
            continue
        lk = np.array([g["like_count"] for g in group], dtype=float)
        # Percentile rank within video; average ranks so the 82% zero mass shares one value.
        order = lk.argsort()
        ranks = np.empty(n)
        ranks[order] = np.arange(n)
        for value in np.unique(lk):
            tie = lk == value
            ranks[tie] = ranks[tie].mean()
        target = ranks / max(1, n - 1)

        secs = _duration_seconds(group[0]["duration"] or "")
        is_short = 1.0 if secs is not None and secs <= 60 else 0.0
        lv = math.log1p(group[0]["view_count"] or 0)
        lc = math.log1p(group[0]["comment_count"] or 0)
        for i, g in enumerate(group):
            age = max(0.0, g["age_h"] or 0.0)
            x.append([i / max(1, n - 1), math.log1p(age), lv, lc, is_short])
            x_len.append(_length_features(g["text"]))
            text.append(g["text"])
            y.append(target[i])
            likes.append(g["like_count"])
            video_idx.append(vi)
            channel.append(g["channel_handle"] or "?")
        video_ids[vi] = _vid

    return Dataset(
        np.array(x, dtype=np.float32), np.array(x_len, dtype=np.float32), text,
        np.array(y, dtype=np.float32), np.array(likes), np.array(video_idx),
        np.array(channel), video_ids,
    )


def _duration_seconds(iso: str) -> int | None:
    import re

    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    h, mi, s = (int(v) if v else 0 for v in m.groups())
    return h * 3600 + mi * 60 + s


def channel_split(data: Dataset, n_test: int = 8, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic channel-level split. Returns (train_mask, test_mask)."""
    channels = np.array(sorted(set(data.channel.tolist())))
    rng = np.random.default_rng(seed)
    test_channels = set(rng.permutation(channels)[:n_test].tolist())
    test = np.array([c in test_channels for c in data.channel])
    return ~test, test
