# YouTube Comment Exposure

Collector, analysis, and paper source for a corpus of **1,345,353 top-level YouTube
comments** from 633 fully-enumerated videos, carrying the post timestamps that public
comment corpora omit.

- **Dataset:** [huggingface.co/datasets/brianhliou/youtube-comment-exposure](https://huggingface.co/datasets/brianhliou/youtube-comment-exposure) (CC BY 4.0)
- **Paper:** [brianhliou.com/publications/youtube-comment-exposure](https://brianhliou.com/publications/youtube-comment-exposure/)
- **Write-up:** [brianhliou.com/posts/comment-likes-are-timing](https://brianhliou.com/posts/comment-likes-are-timing/)

## The finding

Exposure (post time and video scale) accounts for **83.5%** of the ordering skill
available above chance in a comment's like count. What the comment says accounts for
8.2%, or 16.5% if comment length is counted as content rather than exposure. A
384-dimensional sentence encoder adds 0.0015 AUC over a bag of words, so the small
content effect isn't an artifact of a weak text model.

## Layout

Paths are preserved from the monorepo this was extracted from, so the code is identical
to what produced the published numbers.

| path | what it does |
|---|---|
| `studies/comments/collect.py` | quota-bounded, resumable collector (`freeze` / `run` / `status`) |
| `studies/comments/population.py` | freezes and hashes the video population before collection |
| `studies/comments/store.py` | SQLite schema, idempotent writes, per-page checkpoints |
| `studies/comments/features.py` | post-time features and the held-out-channel split |
| `studies/comments/analysis.py` | the predictor ladder and the paired bootstrap |
| `studies/comments/circularity.py` | validity checks on the retrieval-based estimator |
| `studies/comments/generate.py` | the generation experiment (the only module that bills) |
| `studies/comments/export.py` | builds the release files |
| `studies/comments/figures.py` | the paper's figure |
| `studies/comments/populations/` | the frozen manifest pinning all 633 video IDs |
| `studies/comments/results/` | the JSON each published table was read from |
| `paper/` | paper source, bibliography, and build script |

## Reproducing

```bash
python -m venv .venv && .venv/bin/pip install -e '.[analysis,dev]'
.venv/bin/pytest                      # 32 tests, no network
```

Re-collecting needs a YouTube Data API key in `YOUTUBE_API_KEY` and about 15,000 quota
units, which is two days at the free tier:

```bash
python -m studies.comments.collect freeze \
    --config studies/comments/config/stratified.json \
    --out data/runs/comments/stratified-population.json
python -m studies.comments.collect --daily-budget 8000 run \
    --population data/runs/comments/stratified-population.json \
    --db data/runs/comments/stratified.sqlite
```

The committed population manifest pins the exact 633 video IDs and the filters that
selected them, so the sample can be rebuilt or extended without guessing at the rules.
Like counts will differ from the published corpus, since they keep accruing.

Analysis, once you have the corpus (or the released one loaded into the same schema):

```bash
python -m studies.comments.embed                 # ~20 min on Apple Silicon
python -m studies.comments.analysis --embeddings data/runs/comments/embeddings-minilm.npz
```

## Two properties that will bite you

**82.1% of comments have zero likes and the top 1% hold 94.9% of them.** Any summary
that averages raw like counts reports no effect where a large one exists. Use a log or
rank transform. This caught me three times while writing the paper.

**Collection runs smallest-video-first**, so a partial corpus is size-truncated rather
than a random subsample. Interim numbers are pipeline checks, not results.

## License

Code is MIT (see `LICENSE`). The corpus is released separately under CC BY 4.0; comment
text belongs to the people who wrote it, and the dataset card covers the terms and the
limits of the author pseudonymisation.
