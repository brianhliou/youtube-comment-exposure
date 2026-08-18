# Comments — "what makes a comment get liked?"

**Pillar:** Capability (proxy-vs-reality). **Status:** Phase 0 — corpus collection only.
The analysis and any generative work are gated on the 2026-08-31 checkpoint; the
collector runs now because API quota is calendar-bound and cannot be compressed later.

## Question

Everyone believes a YouTube comment's like count says something about the comment. Does
it? Once you condition on *when* it was posted, *which* video it sits under, and *where*
the ranking displayed it, how much signal is left in the text?

## The proxy under audit

**Like count as a measure of comment quality.** This proxy is load-bearing in practice —
it is the training target for every comment-generation paper in the literature, and the
selection criterion in the closest prior work (Dong et al., AAAI 2026, which fine-tunes
Llama-3-8B on the top-10% most-liked comments and reports 3× engagement). It is also
never validated: that 3× is measured by embedding-retrieval against the training
distribution, which a model trained on high-like comments satisfies by construction.

## Substrate

Real YouTube comment threads, fully enumerated per video. The ground truth is millions
of real human votes — a stronger behavioral oracle than a constructed game, and the
reason this study can quantify the proxy's failure rather than merely observe it.

## The exposure confounders

Likes ≈ exposure × quality, and exposure dominates. Four terms the corpus is built to
measure:

| Confounder | Why it dominates | Captured by |
|---|---|---|
| Post time | First-hour comments get orders of magnitude more impressions | `comment.published_at − video.published_at` |
| Displayed rank | "Top comments" ranking *causes* likes, which cause ranking | `relevance_ranks` table |
| Video scale | Median comment on a 10M-view video beats the best on a 10k-view video | video `view_count`, per-video fixed effect |
| Author halo | Verified/known commenters collect likes on identity | `author_channel_id` history |

## The exact metric

**Incremental variance in `log(1 + likes)` explained by text**, over an exposure-only
baseline, measured on **held-out channels**.

Ladder of predictors, each adding one thing:

1. **Video mean** — predict every comment gets its video's mean. The floor.
2. **Exposure only** — post-time bucket + comment length + displayed rank. No text.
3. **Lexical** — TF-IDF ridge over the text.
4. **Semantic** — sentence-embedding ridge.
5. **LLM judge** — zero-shot pairwise ranking, same video, same time bucket.
6. **Fine-tuned scorer** — the reward model, if rungs 3–5 justify building it.

The headline is the gap between rung 2 and the best of 3–5. Everything below rung 2 is
the confound; everything above it is the claim.

## Hypothesis

Text adds little once exposure is controlled — the effect reported in prior work is
mostly post-time and video scale wearing a content costume. Under that outcome the
dissociation is **like-predictable ≠ text-attributable**, and it explains why every
existing comment-generation eval that scores against retrieved neighbours or a simulated
audience reports a large effect: those evals never removed the exposure term.

## Pre-registered analysis

Registered before collection so the result cannot be chosen after seeing it.

1. **Primary.** Incremental R² on `log(1 + likes)` from text features, over
   exposure-only, on held-out channels. Report with a bootstrap CI over channels.
2. **Secondary.** Within-video, within-time-bucket ranking Spearman, text-only, against
   a length + post-time baseline. The baseline is the opponent, not chance.
3. **Replication check.** Re-run the retrieval-based estimator used by Dong et al.
   alongside a real held-out-likes evaluation on the same generations, and report the
   gap between the two.
4. **Kill.** If text adds **< 2% incremental variance**, the generative program is dead
   and that is the finding. No pivot to a bigger model.
5. **Guard.** Report `Spearman(score, comment_length)` for any scorer built. A high
   value means a length detector, not a quality model.
6. **Splits are by channel, never by comment.** Random comment splits leak the video and
   the audience, and every number computed that way is inflated.

### Amendment 2026-08-14 — outcome distribution only

At 315,896 comments collected, **75.4% have zero likes and 86.8% have ≤1**. On a
distribution that degenerate, R² on `log(1 + likes)` is dominated by the zero/non-zero
split rather than by any ordering among liked comments, and small absolute R² values
would be uninterpretable.

Therefore: **(2) becomes primary and (1) becomes secondary.** Within-video,
within-time-bucket ranking Spearman is the headline; incremental R² is still reported,
now alongside a two-part hurdle decomposition (P(any like) and magnitude given ≥1). The
<2% kill threshold moves with it — it now applies to incremental rank correlation over
the exposure-only baseline.

This amendment was made on the **marginal distribution of the outcome alone**, before
any text feature was computed and before any text→likes relationship was examined. The
original plan stands above, unedited.

### Observed exposure structure (complete corpus)

Corpus closed **2026-08-15**: 633/633 videos fully enumerated, none skipped or failed,
**1,345,353 top-level comments** across **34 channels** (two of the 36 resolved channels
contributed no videos inside the comment-count band). Computed before any text feature
exists; this is what a text model has to beat.

Mean `log1p(likes)` by within-video age decile, earliest to latest:

| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|----|
| 0.721 | 0.405 | 0.302 | 0.254 | 0.224 | 0.198 | 0.185 | 0.172 | 0.163 | 0.133 |

Monotonic across all ten deciles, a **5.4× spread with video held fixed**. Comments on
the displayed `order=relevance` top page average **419.5 likes vs 1.13** off-page
(n = 63,202 vs 1,282,151) — partly tautological, since the ranking selects on likes,
but it sizes the visibility concentration the feedback loop produces.

**The interim subsample overstated the age effect.** At 403 videos (the *smallest* in the
population) the decile spread was 8.5×; on the complete corpus it is 5.4×. The prediction
recorded at the time was that adding the large videos would make it *steeper* — it did the
opposite, and the mechanism is an open question for the analysis, not a settled one. The
top-page ratio moved the other way, 140× → 373×.

Zero-inflation also worsened with the full corpus: **82.1%** of comments have zero likes
and 91.9% have ≤1, against 75.4% / 86.8% when the amendment above was written. That
strengthens rather than weakens the case for the rank-based primary metric.

## Results (2026-08-15)

**The pre-registered kill fires.** Five channel splits, two independent text
representations, both metrics below the 2% threshold.

Per-rung AUC for *P(likes ≥ 1)*, averaged over held-out videos:

| seed | exposure | +length | +TF-IDF | +MiniLM | Δ TF-IDF | Δ MiniLM | content share |
|---|---|---|---|---|---|---|---|
| 0 | 0.626 | 0.637 | 0.652 | 0.654 | +0.0152 | +0.0167 | 10.9% |
| 1 | 0.653 | 0.668 | 0.685 | 0.685 | +0.0168 | +0.0169 | 9.1% |
| 2 | 0.645 | 0.663 | 0.672 | 0.675 | +0.0092 | +0.0121 | 6.9% |
| 3 | 0.645 | 0.658 | 0.668 | 0.669 | +0.0100 | +0.0104 | 6.2% |
| 4 | 0.644 | 0.658 | 0.668 | 0.671 | +0.0108 | +0.0134 | 7.8% |
| **mean** | | | | | **+0.0124** | **+0.0139** | **8.2%** |

Mean ΔAUC 1.39% and ΔSpearman 1.89%, both under the 2% kill. A 384-dimensional
sentence encoder buys **+0.0015 AUC over a bag of words** — which is what closes the
question, since "the text model was too weak" is the obvious objection to a lexical
null and a different representation family lands in the same place.

**Headline: ~92% of what determines a comment's rank is not what it says.** It is when
the comment was posted, how large the video is, and where the ranking placed it.

Counter-evidence kept in view: MiniLM's ΔSpearman CI fails to exclude zero on one of
five seeds — the content effect is real on average but not uniformly detectable across
audiences.

### The retrieval estimator (`circularity.py`)

Prior work scores generated comments by averaging the like counts of their K nearest
neighbours in a reference corpus. Two questions were asked of it, using real held-out
comments so ground truth is known.

*Does selecting top-liked text alone reproduce the published ~3× ratio?* **No.** Ratios
run 0.88–1.21 across K ∈ {1,5,20} and both selection rules (absolute top decile, and
top decile within video). One ratio below 1 indicates the measurement is noise-dominated.
**The resemblance-artifact hypothesis is not supported and is withdrawn.**

*Does the estimator order real comments by the likes they actually received?* **Barely.**
Within-video Spearman **0.050**; pooled 0.060. The exposure-only baseline scores 0.19 on
the same task. A metric certifying "3× more engagement" orders real comments about four
times worse than a model knowing only post time and video size.

The surviving claim is therefore about *validity*, not about explaining the published
number: whatever the estimator measures, it correlates near-zero with real like outcomes.
Untested alternative that would revive the original hypothesis — **generated** text may
cluster far more tightly near high-liked reference comments than real held-out text does.
That requires generation to settle and remains open.

### Limits

- A **linear probe on frozen embeddings** bounds linear decodability from a
  general-purpose encoder; a fine-tuned encoder is untested.
- This kills the **SFT-on-top-decile recipe** that was under audit, not all approaches.
- MiniLM is trained on clean prose; comments are short, slangy, multilingual.
- Author history is not yet a feature — it needs a proper time cut to avoid leakage.

## Kill / promote gate

- **Promote** if text carries a real, replicated incremental signal on held-out channels
  *and* the exposure-only baseline is beaten by a clear margin — then the reward model
  and preference-pair construction are worth building.
- **Publish anyway** if it doesn't. A near-zero text effect falsifies a published result,
  explains a field-wide evaluation flaw, and ships the dataset that lets others check.
  This study is designed so the deflation is the paper.

## Corpus

The artifact is the point: **there is no public YouTube comment dataset with
timestamps.** The two large ones (YT-30M, YTCommentVerse — 32M comments) carry
`upvotes` but no `publishedAt` and no reply count, so every model trained on them is
silently fitting the early-comment advantage. This corpus records post time, fetch time,
reply count, video stats, and the displayed ranking.

### Collection design

- **Full enumeration per video, never truncation.** `commentThreads.list` under
  `order=time` returns newest first, so a per-video page cap would drop the *oldest*
  comments — precisely the high-exposure ones. Videos too large to enumerate within
  budget are excluded at freeze time and counted in the manifest's `excluded` block.
- **`order=time` is mandatory.** `order=relevance` is nondeterministic across calls and
  returns a winner-biased slice; using it for collection would destroy the within-video
  comparisons. It is fetched once per video, deliberately, as the rank observation.
- **Top-level comments only.** Replies sit in a different exposure regime (ranked under
  their parent, not against the video). `reply_count` is kept as a covariate. Note that
  `videos.list` `commentCount` *includes* replies; top-level threads measured **70.4%**
  of the reported count over 403 videos, so budget estimates built on it are upper bounds.
- **Collection runs smallest-video-first**, so a *partially* collected corpus is
  size-truncated, not a random sample of the population. Interim numbers are pipeline
  checks, never results. Future freezes should randomise the queue order so partial
  corpora stay representative.
- **Shorts are kept, not filtered.** ~13% of population videos are ≤60s and ~28% are
  ≤180s. Shorts are a different exposure regime (Shorts feed, comments in a drawer,
  compressed arrival window), so `duration` is recorded per video and format is a
  stratification variable at analysis time rather than a collection-time exclusion.
- **The population is frozen before collection.** Membership is hashed into the manifest
  and the database is bound to that hash, so the sample cannot drift mid-collection.

### Running it

Requires `YOUTUBE_API_KEY` in the environment (never logged; the key is a query
parameter, so no request URL is ever put in a message or traceback).

```bash
python -m studies.comments.collect freeze \
    --config studies/comments/config/pilot.json \
    --out data/runs/comments/pilot-population.json
python -m studies.comments.collect run \
    --population data/runs/comments/pilot-population.json \
    --db data/runs/comments/pilot.sqlite
python -m studies.comments.collect status --db data/runs/comments/pilot.sqlite
```

`--daily-budget` is a top-level flag, so it goes **before** the subcommand:
`collect --daily-budget 2000 run --population ...`.

**Quota is per Google Cloud project, and this project is already in use.**
`channel-engine` calls the same API with the same `YOUTUBE_API_KEY` for its geo-block
sweep (see `channel-engine/docs/window-into-china/operations/youtube-api-key-setup.md`),
which is tied to two production incidents. Collecting at the default 9,500/day would
leave it nothing. Either cap this study with `--daily-budget` or — better once the
corpus scales past the pilot — provision a second key in its own project, which that
doc already names as the trigger condition.

`run` works the queue until the day's quota is spent, then exits — re-launch it daily
from cron/LaunchAgent. Quota is 10,000 units/day resetting at **midnight Pacific**;
usage is committed to the database as it is spent, so a crash-loop cannot re-burst a
day's budget. At ~1 unit per 100 comments, a day yields roughly 500k–900k comments;
the pilot population targets ~500k total.

Unresolved channel handles surface in the manifest's `excluded.unresolved_channel`
rather than failing silently — check that count after a freeze.

### Sample design

`config/pilot.json` was the pipeline shakedown: 12 science/tech channels, 12-month
window, 5,000-comment ceiling. Its freeze (2026-08-13, 50 units) validated the pipeline
and failed the sample on three counts, all visible in the manifest:

- **11 usable channels**, and long-tailed — one channel contributed a single video.
  The primary metric bootstraps over *channels*, so effective n was ~7.
- **`too_many_comments: 170`.** The ceiling excluded the highest-traffic videos, which
  are exactly where the early-comment advantage is largest. A study arguing that
  exposure dominates cannot exclude its own strongest cases.
- **One comment culture.** Audience-conditional calibration is untestable on a
  monoculture sample.

`config/stratified.json` supersedes it: ~38 channels across science, tech, gaming,
commentary, music, geopolitics/explainer, and cooking; an 18-month window so
low-frequency channels (CGP Grey) clear the minimum; and a 20,000-comment ceiling,
affordable at ~200 units per video.

Channel handles are resolved at freeze time and misses land in
`excluded.unresolved_channel` rather than failing silently — check that count after a
freeze. Within a channel the `max_videos_per_channel` cap keeps videos in `video_id`
order, which is arbitrary but deterministic, not recency-ordered.

Held-out channels are chosen **after** the freeze and before any modelling, from the
manifest, so the split cannot be tuned against a result.
