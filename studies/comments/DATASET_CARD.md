# YouTube Comment Exposure Corpus

YouTube comments with the field the existing public corpora leave out: **when each
comment was posted**. Two releases live here:

- **v1 (August 2026)**: 1,345,353 top-level comments from 633 fully-enumerated videos
  across 34 English-language channels. Files at the repository root.
- **v2 (October 2026)**: 7,152,754 comments from 6,240 fully-enumerated videos across
  311 English, Japanese and Chinese-language channels, plus a panel that fetched the
  displayed "Top comments" page of 500 new videos twice a day for 21 days (1,462,289
  ranked rows). Files under `v2/`. Described in [v2](#v2-october-2026) below.

Everything from "Why this exists" to "Reproducing it" describes v1 unless it says v2.

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

## v2 (October 2026)

v2 asks how the ranking forms, and whether v1's result holds in other languages. It has
two parts, collected with the same code and released with the same field conventions.
The plan, its hypotheses, and every dated amendment are in `studies/comments/V2.md` in
the code repository.

### Files

| config | file | rows | contents |
|---|---|---|---|
| `v2_bulk_comments` | `v2/bulk/comments.jsonl.gz` | 7,152,754 | one top-level comment per line, v1 fields |
| `v2_bulk_videos` | `v2/bulk/videos.jsonl.gz` | 6,240 | v1 video fields plus `lang` and `marquee` |
| `v2_panel_pages` | `v2/panel/pages.jsonl.gz` | 1,462,289 | one row per comment per fetched top page |
| `v2_panel_videos` | `v2/panel/videos.jsonl.gz` | 500 | panel video metadata, enrollment and status |
| `v2_panel_totals` | `v2/panel/totals.jsonl.gz` | 20,467 | view, like and comment totals per video per period |
| `v2_panel_fetches` | `v2/panel/fetches.jsonl.gz` | 21,597 | one row per attempted page fetch, with outcome |
| | `v2/manifest.json` | | row counts, field lists, SHA-256 and bytes per file |

### Bulk: the replication

Up to 25 videos per channel, published 2025-09-01 to 2026-08-19 with 200–20,000 reported
comments, each enumerated completely as in v1. 311 channels: English (v1's channels and
more), Japanese, and Chinese-language (Taiwan, Hong Kong and overseas). `lang` is the
channel's language as assigned when the channel list was built, not a per-comment
detection. Collected 2026-09-19 to 2026-10-01.

By language: 5,421,217 English, 1,125,472 Japanese and 606,043 Chinese comments. The
share with zero likes is 78.5%, 44.1% and 67.1%, against v1's 82.1%. The Japanese and
Chinese videos are also smaller (median 348 and 299 comments against 798), so those
differences mix language with video size.

### Panel: the ranking over time

500 videos, enrolled within 30 hours of upload between 2026-09-19 and 2026-09-27 (167
English, 167 Japanese, 166 Chinese, from 205 channels, at most five per channel). Twice a
day for 21 days the collector fetched each video's first page of `order=relevance`
comment threads, up to 100, and recorded the rank, likes and post time of each. 487
videos completed; 13 were dropped because comments were turned off, and
`status_reason` records it.

- `period` names the collection run, for example `2026-09-22b`. A period ending in `r`
  is an immediate repeat of a page just fetched, kept to measure noise (`is_repeat`).
- `is_creator` marks the channel owner's own comments, which are usually pinned. The
  analysis drops them and re-ranks the page; the raw author ID that would let you do so
  is not released, so this flag is.
- `comment_age_hours` and `video_age_hours` are the comment's post time and the fetch
  time, each measured from the video's publish time.
- A missing page means the fetch was not attempted; `v2_panel_fetches` lists every
  attempt and its outcome.

`marquee = true` marks two very large channels, @MrBeast and @PewDiePie, added so the
per-channel tables have rows people recognise. One panel video (@MrBeast) carries it, and
the study's pooled estimates exclude it. No bulk video does.

### v2 known limits

**The page is one request's view.** The panel records what the API returned. Immediate
repeats agree (median top-10 Jaccard 1.00 over 1,130 pairs), and a 2026-10-22 test on 250
closed panel videos found the top 20 identical back to back, with and without a
cache-busting request, and again 15 minutes later on 84 of them, with fresh etags; that
test ran on month-old videos, so it does not cover the busier early days.
A spot check of five videos opened logged out in a browser matched the API page about
as well as two API pages 12 hours apart. Logged-in, personalised pages were not
measured.

**Established creators only.** Channels had 300K to 15M subscribers and comments open.
Nothing here describes small channels.

**Panel like counts are a time series, bulk like counts are one observation.** Bulk
comments were fetched once, between 1 and 13 months after the video went up.

## Author identifiers

`author_hash` is a SHA-256 of the author's channel ID with a random salt that was
generated at export and never written down. That prevents linking an author between this
dataset and any other one. v1 and v2 used different salts, so the same author has
different hashes in the two releases; within v2, the bulk and panel files share one salt.

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

For v1:

```bibtex
@misc{liou2026commentexposure,
  title  = {YouTube Comment Exposure: 1,345,353 Comments with Post Timestamps},
  author = {Liou, Brian H.},
  year   = {2026},
  note   = {https://brianhliou.com/publications/youtube-comment-exposure/}
}
```

For v2, cite the same entry with the version, or the write-up:
[brianhliou.com/posts/comment-ranking-keeps-moving](https://brianhliou.com/posts/comment-ranking-keeps-moving/).

The v1 paper reports what the timestamps make measurable: exposure accounts for 83.5% of
the ordering skill available above chance, against 8.2% for comment content.

## Reproducing it

Collector, frozen population manifest, and analysis code are in
[`ai-arena/studies/comments`](.). The population manifest pins the exact 633 video IDs
and the filters that selected them, so the sample can be rebuilt or extended without
guessing at the selection rules.
