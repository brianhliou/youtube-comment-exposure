"""Freeze the video population before any collection starts.

Corpus membership is an experimental parameter, so it is decided once, hashed, and
committed — never discovered as collection proceeds. A sample that grows while it is
being measured cannot be reasoned about afterwards, and the database refuses to serve
a population hash other than the one it was bound to.

**The comment-count band is the load-bearing filter.** ``commentThreads.list`` under
``order=time`` returns newest first, so a per-video page cap would systematically drop
the *oldest* comments — the ones with the largest exposure advantage, which is the
effect the study exists to measure. Rather than truncate, videos too large to
enumerate completely are excluded at freeze time and counted in ``excluded``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.manifest import build_manifest, hash_text, write_manifest
from studies.comments.client import ResourceUnavailable, YouTubeClient
from studies.comments.quota import QuotaLedger

# One `commentThreads.list` page returns at most this many top-level comments.
PAGE_SIZE = 100


@dataclass
class PopulationConfig:
    """The sample definition. Every field changes the population hash."""

    name: str
    channels: list[str]  # handles ("@veritasium") or raw channel ids ("UC...")
    published_after: str  # ISO date; videos older than this are out
    published_before: str  # ISO date; leaves a settling window for like counts
    min_comments: int = 200
    max_comments: int = 5_000
    max_videos_per_channel: int = 20
    max_upload_pages: int = 10  # bounds the walk back through a channel's uploads

    @classmethod
    def load(cls, path: str | Path) -> PopulationConfig:
        data = json.loads(Path(path).read_text())
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


@dataclass
class FreezeResult:
    videos: list[dict[str, Any]]
    channels: list[dict[str, Any]]
    excluded: dict[str, int] = field(default_factory=dict)
    units_spent: int = 0


def population_hash(config: PopulationConfig, video_ids: list[str]) -> str:
    """Hash of the sample definition plus the exact members it resolved to."""
    payload = json.dumps(
        {"config": config.as_dict(), "video_ids": sorted(video_ids)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hash_text(payload)


def estimated_units(comment_count: int) -> int:
    """Units to fully enumerate a video: one per page, plus the relevance probe.

    A deliberate over-estimate, roughly 1.4×. ``comment_count`` comes from
    ``videos.list`` ``statistics.commentCount``, which counts replies, while
    ``commentThreads.list`` pages only top-level threads — measured at **70.4%** of the
    reported count over 403 collected videos. (A two-video sample put it at 48%; that
    was small-sample noise.) Budgeting against the upper bound is the safe direction:
    it can only finish early.
    """
    return max(1, -(-comment_count // PAGE_SIZE)) + 1


def _resolve_channel(client: YouTubeClient, ref: str) -> dict[str, Any] | None:
    """Resolve a handle or channel id to its id + uploads playlist."""
    params: dict[str, Any] = {"part": "contentDetails,snippet"}
    if ref.startswith("@"):
        params["forHandle"] = ref
    else:
        params["id"] = ref
    body = client.get("channels", params)
    items = body.get("items") or []
    if not items:
        return None
    item = items[0]
    return {
        "handle": ref,
        "channel_id": item["id"],
        "title": item["snippet"].get("title"),
        "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def _channel_video_ids(
    client: YouTubeClient,
    ledger: QuotaLedger,
    uploads_playlist: str,
    config: PopulationConfig,
) -> tuple[list[str], int]:
    """Walk a channel's uploads newest-first; return ids inside the date window.

    Also returns how many were seen but fell outside the window, so exclusions stay
    visible rather than looking like the channel simply had fewer videos.
    """
    ids: list[str] = []
    outside = 0
    token: str | None = None
    for page in range(config.max_upload_pages):
        if not ledger.can_spend(1):
            break
        body = client.get(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": 50,
                "pageToken": token,
            },
        )
        ledger.spend(1)
        oldest_seen = None
        for item in body.get("items", []):
            details = item["contentDetails"]
            published = details.get("videoPublishedAt")
            if not published:
                continue
            oldest_seen = published
            if config.published_after <= published[:10] <= config.published_before:
                ids.append(details["videoId"])
            else:
                outside += 1
        token = body.get("nextPageToken")
        # Uploads are newest-first: once we are past the window there is nothing older
        # left worth paging for.
        if not token or (oldest_seen and oldest_seen[:10] < config.published_after):
            break
        del page
    return ids, outside


def _video_stats(
    client: YouTubeClient, ledger: QuotaLedger, video_ids: list[str]
) -> list[dict[str, Any]]:
    """Fetch snippet + statistics for up to 50 ids per unit."""
    out: list[dict[str, Any]] = []
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start : start + 50]
        if not ledger.can_spend(1):
            break
        body = client.get(
            "videos",
            {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "maxResults": 50},
        )
        ledger.spend(1)
        fetched_at = datetime.now(UTC).isoformat()
        for item in body.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            out.append(
                {
                    "video_id": item["id"],
                    "channel_id": snippet.get("channelId"),
                    "published_at": snippet.get("publishedAt"),
                    "title": snippet.get("title"),
                    "category_id": snippet.get("categoryId"),
                    "duration": item.get("contentDetails", {}).get("duration"),
                    "view_count": _as_int(stats.get("viewCount")),
                    "like_count": _as_int(stats.get("likeCount")),
                    "comment_count": _as_int(stats.get("commentCount")),
                    "stats_fetched_at": fetched_at,
                }
            )
    return out


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def freeze(
    client: YouTubeClient, ledger: QuotaLedger, config: PopulationConfig
) -> FreezeResult:
    """Resolve the configured channels into a filtered, frozen video list."""
    channels: list[dict[str, Any]] = []
    excluded = {
        "outside_window": 0,
        "unresolved_channel": 0,
        "no_comment_count": 0,
        "too_few_comments": 0,
        "too_many_comments": 0,
        "over_channel_cap": 0,
    }
    start_units = client.units_spent
    candidates: list[tuple[str, str]] = []  # (channel_ref, video_id)

    for ref in config.channels:
        if not ledger.can_spend(1):
            break
        try:
            resolved = _resolve_channel(client, ref)
        except ResourceUnavailable:
            resolved = None
        ledger.spend(1)
        if resolved is None:
            excluded["unresolved_channel"] += 1
            continue
        channels.append(resolved)
        ids, outside = _channel_video_ids(client, ledger, resolved["uploads_playlist"], config)
        excluded["outside_window"] += outside
        candidates.extend((ref, vid) for vid in ids)

    handle_by_video = {vid: ref for ref, vid in candidates}
    stats = _video_stats(client, ledger, [vid for _, vid in candidates])

    kept_per_channel: dict[str, int] = {}
    videos: list[dict[str, Any]] = []
    for video in sorted(stats, key=lambda v: (v["channel_id"] or "", v["video_id"])):
        count = video["comment_count"]
        if count is None:
            excluded["no_comment_count"] += 1
            continue
        if count < config.min_comments:
            excluded["too_few_comments"] += 1
            continue
        if count > config.max_comments:
            excluded["too_many_comments"] += 1
            continue
        channel = video["channel_id"] or ""
        if kept_per_channel.get(channel, 0) >= config.max_videos_per_channel:
            excluded["over_channel_cap"] += 1
            continue
        kept_per_channel[channel] = kept_per_channel.get(channel, 0) + 1
        video["channel_handle"] = handle_by_video.get(video["video_id"])
        videos.append(video)

    return FreezeResult(
        videos=videos,
        channels=channels,
        excluded=excluded,
        units_spent=client.units_spent - start_units,
    )


def write_population(path: str | Path, config: PopulationConfig, result: FreezeResult) -> Path:
    """Write the frozen population manifest. Committed; the corpus itself is not."""
    ids = [v["video_id"] for v in result.videos]
    manifest = build_manifest(
        "comments",
        {
            **config.as_dict(),
            "population_hash": population_hash(config, ids),
            "n_videos": len(ids),
            "n_channels": len(result.channels),
            "estimated_units": sum(estimated_units(v["comment_count"]) for v in result.videos),
            "estimated_comments": sum(v["comment_count"] for v in result.videos),
            "excluded": result.excluded,
            "freeze_units_spent": result.units_spent,
        },
    )
    manifest["channels"] = result.channels
    manifest["videos"] = result.videos
    return write_manifest(path, manifest)


def load_population(path: str | Path) -> dict[str, Any]:
    """Read a frozen manifest and verify its hash still matches its members."""
    data = json.loads(Path(path).read_text())
    config = PopulationConfig(
        **{k: v for k, v in data["config"].items() if k in PopulationConfig.__dataclass_fields__}
    )
    ids = [v["video_id"] for v in data["videos"]]
    expected = population_hash(config, ids)
    recorded = data["config"]["population_hash"]
    if expected != recorded:
        raise ValueError(
            f"population manifest is inconsistent: members hash to {expected[:12]} "
            f"but the manifest records {recorded[:12]} — the file was edited after freeze"
        )
    return data
