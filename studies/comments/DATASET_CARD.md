# YouTube Comment Exposure Corpus v1

1,345,353 top-level YouTube comments from 633 fully-enumerated videos across 34
channels, with the field the existing public corpora leave out: **when each comment was
posted**.

## Why this exists

The two large public YouTube comment datasets, YT-30M and YTCommentVerse (~32M comments
between them), carry like counts with no timestamps and no reply counts. A like count
without a post time is close to unusable for anything causal, because a comment posted
in a video's first hour gets far more impressions than one posted a week later. Any
model trained on those corpora is fitting that head start without being able to see it.

This corpus records post time, fetch time, reply count, video statistics, and the
position YouTube's own "Top comments" ranking gave each comment.

## Files

| file | rows | contents |
|---|---|---|
| `comments.jsonl.gz` | 1,345,353 | one top-level comment per line |
| `videos.jsonl.gz` | 633 | video metadata and collection counts |
| `manifest.json` | | row counts, field lists, SHA-256 per file |

### Comment fields

`comment_id`, `video_id`, `author_hash`, `text`, `like_count`, `reply_count`,
`published_at`, `fetched_at`, `age_hours` (comment publish minus video publish),
`relevance_rank` (position on the displayed top page, null if absent).

### Video fields

`video_id`, `channel_handle`, `published_at`, `title`, `category_id`,
`duration_seconds`, `is_short`, `view_count`, `like_count`, `comment_count`,
`stats_fetched_at`, `collected_comments`.

## How it was collected

Channels were picked to span science, tech, gaming, commentary, music, geopolitics and
cooking, then filtered to videos published between 2025-02-13 and 2026-07-14 with
200–20,000 reported comments. That window leaves at least 30 days for like counts to
settle before collection.

**Every video is enumerated completely.** `commentThreads.list` returns newest-first
under `order=time`, so a per-video page cap would drop the oldest comments, which are
exactly the high-exposure ones. Videos too large to enumerate inside the API's daily
quota were excluded when the population was frozen, and the counts are recorded in the
population manifest rather than dropped silently.

Collection ran 2026-08-13 to 2026-08-15 against the YouTube Data API v3. All 633 videos
completed; none were skipped or failed.

## What's in the corpus, in aggregate

82.1% of comments have zero likes and 91.9% have one or fewer. Mean `log1p(likes)` by
within-video comment-age decile runs 0.721 down to 0.133, monotonically. Comments
appearing on the displayed top page average 419.5 likes against 1.13 for those that
don't.

## Known limits

**Top-level comments only.** Replies sit under their parent rather than competing for
the video's ranking, so they're a different exposure regime. `reply_count` is kept as a
covariate; reply text isn't collected.

**The comment-count band is a real restriction.** Videos above 20,000 comments are
absent, and those are where exposure effects are largest. Findings shouldn't be
extrapolated to the largest videos on the platform.

**Like counts are a single observation.** `fetched_at` records when, and a comment
collected on day one of the run has had less settling time than one collected on day
three.

**English-dominant, creator-skewed.** 34 channels is enough for a held-out-channel
split and nowhere near a representative sample of YouTube.

**Collection order was smallest-video-first**, so any partial copy of this corpus is
size-truncated rather than a random subsample.

## Author identifiers

`author_hash` is a SHA-256 of the author's channel ID with a random salt that was
generated at export and never written down. That prevents linking an author between this
dataset and any other one.

It does not make the data anonymous. Comment text is public, indexed, and searchable, so
anyone determined can recover an author from the text. Treat this as pseudonymized
public content, not as de-identified data.

## Licensing and terms

Comment text belongs to the people who wrote it. This corpus is released for research
under CC BY 4.0 as applied to the collection, the schema, and the derived fields, which
follows the precedent set by YTCommentVerse.

YouTube's API Terms of Service restrict storage and redistribution of API data, and this
release is in the same grey area every public YouTube comment corpus occupies. Anyone
reusing it should read those terms and reach their own conclusion. Takedown requests for
specific comments will be honored.

## Citation

```bibtex
@misc{liou2026commentexposure,
  title  = {YouTube Comment Exposure: 1,345,353 Comments with Post Timestamps},
  author = {Liou, Brian H.},
  year   = {2026},
  note   = {https://brianhliou.com/publications/youtube-comment-exposure/}
}
```

The paper reports what the timestamps make measurable: exposure accounts for 83.5% of
the ordering skill available above chance, against 8.2% for comment content.

## Reproducing it

Collector, frozen population manifest, and analysis code are in
[`ai-arena/studies/comments`](.). The population manifest pins the exact 633 video IDs
and the filters that selected them, so the sample can be rebuilt or extended without
guessing at the selection rules.
