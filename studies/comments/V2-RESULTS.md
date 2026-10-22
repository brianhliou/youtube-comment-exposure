# Comments v2: results (analysis run 2026-10-09, final re-run 2026-10-21)

Pre-registered plan: `studies/comments/V2.md` (unchanged). Primary endpoint day 7; days 14
and 20 are registered secondary endpoints (V2.md, "secondary endpoints"). Nothing here is
committed.

**Two snapshots; every number below says which.**

- **10-09 snapshot**: a `.backup` of `data/runs/comments/panel-v2.sqlite` taken 2026-10-09
  14:46 PDT (last panel run `2026-10-09b`). Outputs in `results/v2/` (top level). Kept for
  the record; superseded for day 14 and day 20.
- **10-21 snapshot (final)**: `data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite`,
  `.backup` taken 2026-10-21 22:49 PDT. The panel was complete: last period `2026-10-17b`,
  every video closed at "window elapsed" (en 167, ja 156, zh 164), 13 dropped as
  `commentsDisabled` (ja 11, zh 2). Outputs in `results/v2/final/`, produced by
  `results/v2/final/run-panel.sh` (run log `final/run.log`, all exit 0).
- **Study B** ran on `data/runs/comments/v2.sqlite` (closed 10-01). The H6/H8 chain finished
  2026-10-09 21:31 (`results/v2/bulk.log` ends `DONE`); summary `results/v2/bulk-summary.json`.

**Bottom line.** The final top comments were mostly posted early: 84% of the day-7 top 20
within 24 h, and still 82% of the day-20 top 20 (H2 holds at every endpoint). The page does
not lock. Median lock-in is 76 h at day 7, 146 h at day 14 and 138 h at day 20; consecutive
pages 12 h apart keep only about 0.80 top-10 overlap from day 4 to day 21. H1 fails at every
endpoint, though it never reaches its >7-day kill condition. H3 fails (1.85×, CI now spans 1).
H4 holds at days 7 and 14 and fails for ja at day 20. H5 fails at day 7 and passes on the
point estimate only at days 14 and 20 (CIs span zero). Study B: H6 holds for every language
and rung (86–95% exposure share). H7 fails for ja and zh. H8 holds for en and ja and fails for
zh on the registered MiniLM rung (0.602).

## Headline numbers

Commands (10-21 run, `final/run-panel.sh`):

- `panel-dayN.json`: `.venv/bin/python3 -m studies.comments.panel_analysis --db <snapshot> --endpoint-hours {168|336|480} --json studies/comments/results/v2/final/panel-day{7|14|20}.json`
- `panel-supplement.json`: `.venv/bin/python3 -m studies.comments.v2_panel_supplement --db <snapshot> --hours 168 336 480 --json studies/comments/results/v2/final/panel-supplement.json`

The supplement holds the plan-required numbers that `panel_analysis.py` does not produce:
per-language drops, entry-age CIs, the H5 CI, per-genre results, marquee, the gate and the
feedback CI. The 10-09 commands were the same with `results/v2/` as the output directory.

### Study A: panel, day 7 (primary)

The day-7 lock-in, persistence, entry-age and late-entrant numbers are **identical in both
snapshots** (every video had passed 168 h by 10-09 except 17 with no endpoint observation,
and the 10-21 run excludes the same 17). Reliability and feedback use every observation, so
they changed; both versions are shown.

| Measure | Value | Source |
|---|---|---|
| Kill gate: videos with ≥7 observation days (non-marquee) | 482, and 239 in the lock-in analysis; ≥200, so **pass** | `final/panel-supplement.json` `gate_and_channels`; `final/panel-day7.json` `lockin.n` |
| Reliability floor (immediate repeats) | **10-21:** 1,130 pairs; median top-10 and top-20 Jaccard 1.00 (IQR 1–1); 85 pairs below 1; minimum 0.25; median repeat gap 46 s. (10-09: 860 pairs, 78 below 1, min 0.43, gap 56 s.) | `final/panel-day7.json` `reliability`; `final/panel-supplement.json` `reliability_extras` |
| Lock-in, first top-10 Jaccard ≥0.8 with the endpoint | median **76.0 h** (IQR 40.0–141.9), cluster CI95 [66.8, 93.4], n=239 | `final/panel-day7.json` `lockin` |
| Sustained lock-in | median 150.9 h | same |
| Locked by 72 h | 47.7% of videos; 30 locked at first observation; 27 never crossed before the endpoint | supplement `endpoint_168h.lockin` |
| Lock-in by language | en 82.0 h (n=119), ja 69.2 h (n=73), zh 79.5 h (n=47) | `panel-day7.json` `lockin.by_lang` |
| Lock-in by format | Shorts 63.9 h (n=40), long-form 79.5 h (n=199); long minus short **15.6 h, CI95 [−24.5, 39.2]** | day-7; supplement `long_minus_short_*` |
| Persistence, mean top-20 Jaccard vs endpoint | 0–6 h 0.24 · 24–48 h 0.56 · 48–72 h 0.63 · 72–96 h 0.68 · 96–120 h 0.71 · 120–144 h 0.74 · 144–168 h 0.80. Spearman rises 0.61 → 0.91. | `panel-day7.json` `persistence` |
| Entry age of the endpoint top-20 (n=4,780) | within 1 h **34%** [31, 37] · 6 h **65%** [62, 69] · 24 h **84%** [82, 87]; median 2.4 h | `panel-day7.json` `entry_age`; supplement (cluster bootstrap B=1000) |
| Entry age by language, within 24 h | en 84% [81, 87] · ja 85% [79, 90] · zh 83% [77, 88] | supplement `endpoint_168h.entry_age` |
| Entry age by language, within 1 h | en 36% · ja 38% · zh 24% [18, 30] | same |
| Late entrants (first top-20 appearance at ≥72 h) | median 6 per video; 97% of videos have at least one; 27% were posted late, so about 73% are old comments that rose | `panel-day7.json` `late_entrants` |
| Feedback loop, like gain per hour at rank ≤10 vs rank >50 | **10-21:** 2,484 movers across 163 channels; 0.54 vs 0.29; ratio **1.85, CI95 [0.96, 3.22]**. (10-09: 2,377 movers, ratio 1.89 [1.04, 3.48].) | `final/panel-day7.json` `feedback.within_comment`; supplement `feedback` |

Lock-in by genre (day 7) is in the supplement under `per_genre_first_crossing_h`.
Entertainment, gaming and music are fastest (median 55–69 h). News, finance, education and
sports are slowest (119–153 h), and several of those cells have n<10.

### Pre-registered hypotheses, day 7

| H | Registered test | Result | Verdict |
|---|---|---|---|
| H1 | median lock-in ≤72 h | 76.0 h, CI [66.8, 93.4]. By language: ja passes, en and zh fail. | **Fails**, narrowly |
| H2 | ≥70% of the endpoint top-20 entered within 24 h and ≥30% within 1 h | 84% and 34% | **Holds** |
| H3 | feedback ratio ≥3× | 1.85×, CI [0.96, 3.22] | **Fails**; the CI also includes no effect |
| H4 | each language within ±1 day and ±10 points of pooled | all three within the bounds in the code's check | **Holds** (see deviation 2) |
| H5 | Shorts lock in ≥1 day earlier | 15.6 h, CI spans 0 | **Fails** |
| Endpoint rule | if H1 fails at 72 h and the curve has not flattened, wait for day 14 | both conditions met | **Post waits for day 14** (done below) |

### Study A: day 14 (registered secondary; the endpoint the plan's rule points to)

Source: 10-21 snapshot, `final/panel-day14.json` and `final/panel-supplement.json`
`endpoint_336h`. n=243 (10-09 snapshot: n=240; every number below moved by <2 h or <1 point).

- **Lock-in:** median **146.0 h** (IQR 57.0–290.5), CI [117.0, 176.0]. Sustained median 321.1 h. 30.9% locked by 72 h.
  - By language: en 146.0 h (n=119), ja 165.1 h (n=77), zh 129.0 h (n=47).
  - By format: Shorts 122.8 h, long-form 147.5 h. The difference is 24.7 h, CI [−46.0, 117.0], so H5 passes the point check but the CI spans zero.
- **Persistence against the day-14 page:** 0.66 at 144–168 h and 0.74 at 240–336 h. The day-7 page agrees with the day-14 page only about as well as the 48 h page agrees with the day-7 page.
- **Entry age:** 34.6% [31.3, 37.8] within 1 h, 65% within 6 h, 82.9% [80.1, 85.4] within 24 h; zh 79.8% within 24 h. H2 holds.
- **Late entrants:** median 11 per video.
- **Hypotheses:** H1 fails, H2 holds, H3 fails (the same movers), H4 holds, H5 holds on the point estimate only.

### Study A: day 20 (registered secondary)

Source: 10-21 snapshot, `final/panel-day20.json` and supplement `endpoint_480h`. **n=250**;
the gate passes. (The 10-09 run had a partial cohort, n=75, median 175 h; that number is
superseded and should not be quoted.)

- **Lock-in:** median **137.8 h** (IQR 54.7–332.0), CI [112.0, 176.0]. Sustained median 464.0 h. 34% locked by 72 h; 19 never crossed.
  - By language: en 116.3 h (n=121), ja 188.5 h (n=78), zh 150.8 h (n=51). **H4 fails for ja** (more than a day from pooled).
  - By format: Shorts 97.0 h, long-form 140.2 h; difference 43.2 h, CI [−146.0, 96.0]. H5 passes on the point estimate only.
  - Drops: under the minimum comment count en 45, ja 78, zh 109; no endpoint observation ja 11, zh 6; marquee 1.
- **Persistence against the day-20 page:** 0.21 at 0–6 h, 0.51 at 24–48 h, 0.645 at 144–168 h, 0.774 at 336–504 h.
- **Entry age (n=5,000):** 32.9% [29.6, 36.3] within 1 h, 63.0% within 6 h, 81.6% [78.7, 84.2] within 24 h; median 2.74 h. By language at 24 h: en 81.4%, ja 83.4%, zh 79.3%; zh 22.8% within 1 h. H2 holds.
- **Late entrants:** median 13 per video; every included video has at least one.
- **Hypotheses:** H1 fails (but 137.8 h is under the >7-day kill line), H2 holds, H3 fails, H4 fails for ja, H5 point estimate only.

**Reading.** The lock-in estimate rose from day 7 to day 14 (76 → 146 h) and then stopped
rising (138 h at day 20, CIs overlapping). So the median is not simply chasing the endpoint;
it settles near six days. But the page itself does not settle: consecutive pages 12 h apart
keep about 0.80 top-10 Jaccard from day 4 to day 21 (exploratory, below), and the day-7 page
shares only 0.645 top-20 Jaccard with the day-20 page. The defensible claim is about **who**
ends up on top: comments posted in the first hours. It is not about **when** the page is
decided.

### Study B: bulk replication (v2.sqlite)

#### H7: outcome distribution

Source: `results/v2/final/bulk-outcome.json`, written by `results/v2/final/bulk_outcome.py`
(2026-10-21). It replaces the 10-09 scratch script with the same definitions and reproduces
its numbers exactly. It joins `comments` to `videos` where `status='done'` and maps each
channel to a language with `config/panel-channels.json`. For each language it reports
`mean(like_count==0)`, `mean(like_count<=1)`, and the share of all likes held by the top 1%
of comments (sorted descending, k=round(1% of n)). Marquee channels contribute 0 comments to
v2.sqlite.

| Language | Comments | Zero-like share | ≤1-like share | Top 1% share of likes | H7 (75–90%) |
|---|---|---|---|---|---|
| en | 5,421,234 | 78.5% | 90.1% | 92.4% | **holds** |
| ja | 1,125,474 | 44.1% | 62.2% | 60.6% | **fails** |
| zh | 606,046 | 67.1% | 80.9% | 71.1% | **fails** |
| pooled | 7,152,754 | 72.1% | 85.0% | 86.8% | — |
| v1 reference (paper) | 1,345,353 | 82.1% | 91.9% | 94.9% | — |

**Sanity check on ja.** The low share is not one outlier. Per-video zero-like shares have a
median of 43% for ja, 59% for zh and 76% for en, over 1,683, 1,093 and 3,442 videos
(`bulk-outcome.json` `median_video_zero_like_share`). ja and zh videos are smaller, with a
median of 348 and 299 comments against 798 for en. Video size and language are confounded
here, and the plan has no control for it. Any claim about language should say so.

The model runs below see slightly fewer rows (en 5,421,217 comments on 3,425 videos) because
`features.load` drops videos it cannot featurise; the difference is 17 videos in en, 2 in ja,
3 in zh.

#### H6 and H8: exposure share and content-only AUC

Source: `results/v2/bulk-summary.json` (from `v2_bulk.py summary`, over
`results/v2/bulk/{tfidf|embed}-{lang}-seed{0..4}.json`). Exposure share is v1's
decomposition, (exposure AUC − 0.5)/(text-rung AUC − 0.5). **Brackets are [min, max] across
the five channel-split seeds, not a confidence interval**, the same convention as v1's
"range across splits". The registered H6 rung is v1's MiniLM rung (`embed`); word TF-IDF
(`tfidf`) is v1's other text rung; character n-grams (`chartfidf`) are exploratory.

| Language | Rung | Exposure share, mean [min, max] | Content-only AUC (mean of 5) | H6 (≥70%) | H8 (<0.60) |
|---|---|---|---|---|---|
| en | MiniLM (registered) | 86.4% [82.1, 91.0] | 0.588 | holds | holds |
| en | word TF-IDF | 86.4% [83.0, 90.0] | 0.588 | holds | holds |
| ja | MiniLM (registered) | 90.9% [86.7, 93.8] | 0.582 | holds | holds |
| ja | word TF-IDF | 94.7% [92.5, 97.9] | 0.565 | holds | holds |
| ja | char n-gram (exploratory) | 87.1% | 0.605 | — | fails |
| zh | MiniLM (registered) | 90.0% [84.6, 95.8] | **0.602** | holds | **fails** |
| zh | word TF-IDF | 92.0% [87.1, 97.9] | 0.584 | holds | holds |
| zh | char n-gram (exploratory) | 86.6% | 0.625 | — | fails |
| pooled | word TF-IDF | 90.6% [84.3, 96.8] | 0.580 | holds | holds |

Corpus per language (`bulk-summary.json` meta): en 5,421,217 comments / 3,425 videos / 161
channels; ja 1,125,472 / 1,681 / 80; zh 606,043 / 1,090 / 70; pooled 7,152,732 / 6,196 / 311.
Exposure-rung AUC: en 0.656, pooled 0.675.

Per-seed content-only AUC for zh MiniLM: 0.624, 0.609, 0.573, 0.600, 0.606 (four of five at
or above 0.60). For zh word TF-IDF: 0.593, 0.586, 0.580, 0.573, 0.586.

**Reading.** H6 replicates v1 in all three languages: once exposure (age at posting, video
size) is in the model, text adds little. v1 reported 83.5% (81.7–86.1%) on its full ladder
(exposure + length + TF-IDF + MiniLM); the v2 ladders are separate per text rung, so the
numbers are close but not the same quantity. H8's zh failure is narrow (0.602 against a 0.60
line) and comes from an English-only encoder; the tokenizer note in deviation 6 cuts both
ways, since better CJK text features (char n-grams) score higher, not lower.

## Exploratory (not pre-registered)

- **Character n-gram TF-IDF rungs** for ja and zh (deviation 6). Table above.
- **Adjacent-page overlap** (10-21 snapshot, `final/panel-supplement.json`
  `exploratory_adjacent_overlap`): mean top-10 Jaccard between consecutive observations. 0.55
  at 12–24 h, 0.69 at 24–48 h, 0.78 at 72–96 h, then 0.81, 0.81, 0.80, 0.79, 0.80, 0.815 and
  0.796 through 504 h+ (n=182 in the last bin). The share of consecutive pairs at or above the
  0.8 lock-in threshold stays near 0.66–0.70 from day 4 on: about a third of consecutive pages
  keep differing by more than the lock-in threshold for as long as the panel watched.
- **Late posts in the final top 3** (10-21 snapshot, `final/late-top3.json` from
  `final/late_top3.py`): of 1,338 final top-3 slots on 482 closed non-marquee videos
  (creator's own comments excluded), 15.5% went to comments posted ≥24 h after the video
  (150 videos), 9.1% ≥48 h (104 videos), 2.9% ≥7 days (34 videos). Of the 122 posted ≥48 h,
  45 were first seen below rank 10 (median first-seen rank 6).
- **Feedback, alternative forms** (10-21): the ratio per period is 1.83. The median of the
  per-comment ratios is 0.90. 1,296 of 2,484 movers had zero or negative gain at rank 51+. The
  pooled 1.85 is carried by a minority of comments.
- **Does the movement accumulate?** (10-21 snapshot, `final/lag-overlap.json` from `final/lag_overlap.py`, the 250 noise-floor videos, from 96 h on): mean top-10 Jaccard between two looks is 0.81 at 12 h, 0.75 at 24 h, 0.72 at 72 h, 0.70 at 120 h, 0.68 at 168 h and 0.66 at 240 h. Of the 8,947 comments that leave the top 10 in one 12-hour step, the median lands at rank 13, 30% are back at the next look and 46% within two. Most 12-hour movement is swapping at the edge of the top 10; the cumulative drift is much slower.
- **v1 arrival table per language** (`bulk-outcome.json`
  `exploratory_mean_log1p_likes_by_age`): mean log1p(likes) falls with age at posting in every
  language (en 0.85 at <1 h to 0.15 at >30 d; ja 1.30 to 0.66; zh 1.27 to 0.21).

## Deviations from the plan

1. **Hours, not days.** `panel_analysis.py` (written 09-19, before data) works in hours since upload. The day-7 endpoint is 168 h. Its observation is the first at or after 168 h, a median 6.9 h late (supplement `endpoint_obs_minus_target_h`).
2. **How the code checks H4 and H5.**
   - H4 checks lock-in ±24 h and the **24 h** entry share ±0.10 only. The 1 h and 6 h shares are reported with CIs in the supplement but are not part of the check.
   - H5 compares the long-form median with the Shorts median, ≥24 h. The CI comes from the supplement.
3. **The reliability "threshold clears" test is median top-10 Jaccard ≥0.9 in code.** The floor came out at 1.00. Repeats were a median 46 s apart, so YouTube may have served them from a cache. The day-3 logged-out spot check found top-10 overlap of only 5–10 of 10. The true noise floor may be lower than 1.00, which would make the drift partly measurement noise. **Tested 2026-10-22** (V2.md amendment, `v2_noise_floor.py`, `final/noise-floor.json`): 250 closed panel videos (26–34 days old) fetched standard, standard and cache-busted back to back, then again at 15/60/120 min. Back-to-back top-10 and top-20 Jaccard 1.00 in all 250 pairs of each kind; 15 min, 84 videos, 1.00 in every pair, standard and busted, with different etags in 96–98%. Share of the observed movement (adjacent 12 h top-10 0.805; day-7 vs day-20 top-20 0.645) inside the floor: 0. Limits: month-old videos only, API only (the logged-out gap stands). The 60 and 120 min arms finish ~12:15 PDT 10-22; read them from `final/noise-floor.json`.
4. **Feedback is measured per hour.** The plan text says per period. The per-period ratio (1.83) is reported as exploratory and agrees.
5. **The per-channel table in `panel_analysis.py` skips marquee channels.** The plan says marquee channels appear in it. The marquee rows are in the supplement instead. @MrBeast `v9QtM6qnG50` locks in at 172 h (day 7), 340 h (day 14) and 484 h (day 20). @PewDiePie has no qualifying video.
6. **Study B added a content-only rung.**
   - H8 needs a rung v1 never had: TF-IDF or MiniLM features with no exposure or length features. Added in `v2_bulk.py`.
   - v1's token pattern `\b\w\w+\b` treats an unspaced CJK run as a single token. The registered word TF-IDF therefore handicaps text for ja and zh, which biases H6 and H8 toward passing.
   - A character n-gram TF-IDF (`char_wb`, 1–3) is run alongside it and labelled **exploratory**.
   - all-MiniLM-L6-v2 is an English-only encoder. It is v1's encoder, so it is run as registered. It is weak for ja and zh, with the same bias.
7. **Study B language split.** Channels map to a language through `config/panel-channels.json`. "Pooled" means all non-marquee channels. `v2_bulk.load_lang` copies `features.load` and adds a channel filter. Splits, alpha grid and seeds are v1's (`analysis.channel_split`, `ALPHAS`, seeds 0–4).
8. **Bulk freeze window.** The plan says 2025-09-01..2026-08-31. The frozen config used `published_before 2026-08-19`. This was set at freeze time, before any analysis, and is recorded here.
9. **Enrollment.** Enrollment fell below 40 per day on 09-25..27. The 167-per-language target was still reached (zh 166). 52 enrolled channels had no upload in the 30 days before the last discovery: en 32, ja 5, zh 15 (supplement `gate_and_channels`).
10. **Latent bug in the registered code.** `panel_analysis._bin_label` raises ValueError for ages ≥504 h because of `zip(..., strict=True)` over unequal lists. The registered outputs never reach that age, because observations are capped at the endpoint, so they are unaffected. The supplement wraps the function. Not fixed in `panel_analysis.py`.
11. **Snapshots.** Study A ran on `.backup` copies of the WAL database, never the live file: 10-09 14:46 and 10-21 22:49 (see top). The 10-21 copy is the final record.
12. **Pooled MiniLM rung not run.** It needs 7.15M embeddings in memory. Pooled H6/H8 rest on word TF-IDF; the three per-language MiniLM ladders cover the registered per-language tests.
13. **Day-0 timeline (corrected 10-22).** V2.md was committed at 11:44 PDT on 09-19, 33 min after the first enrollment fetch (11:11) and before any second fetch (13:00). The primary-endpoint amendment (15:36) and `panel_analysis.py` (16:49) came after that 13:00 second look, not before "any second observation" as both say. Thresholds unchanged. See the V2.md amendment of 2026-10-22.

## Not run, or not finished

- **The logged-out reliability check at scale.** Only the day-3 five-video spot check exists (deviation 3). The 2026-10-22 noise-floor test (below) covers the API side only.
- **A control for video size in H7** (language and video size are confounded).
- **Dataset release.** Staged 2026-10-22 by `v2_export.py` (850 MB, six files, manifest with SHA-256), not uploaded.

## Before publishing

1. The lock-in claim is not supported: H1 fails at every endpoint. "Top comments were posted early" is supported at every endpoint.
2. H8 fails for zh on the registered rung, narrowly. Say so; don't lead with the word TF-IDF pass.
3. H7 fails for ja and zh, and video size is confounded with language.
4. The reliability floor is untested logged-out (deviation 3).

## Files (uncommitted)

- `studies/comments/v2_bulk.py`
- `studies/comments/v2_panel_supplement.py`
- `studies/comments/results/v2/`: `panel-day{7,14,20}.json`, `panel-supplement.json` (10-09), `bulk/`, `bulk.log`, `bulk-summary.json`, `run-bulk.sh`
- `studies/comments/results/v2/final/`: `run-panel.sh`, `run.log`, `panel-day{7,14,20}.{json,txt}`, `panel-supplement.{json,txt}`, `bulk_outcome.py`, `bulk-outcome.json`, `late_top3.py`, `late-top3.json`
- `data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite` (gitignored)
- this file
