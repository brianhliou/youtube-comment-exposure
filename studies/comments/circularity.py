"""Does the retrieval estimator measure likability, or resemblance to liked text?

Prior work (Dong et al., AAAI 2026) evaluates generated comments by embedding each one,
retrieving its K nearest neighbours from a reference corpus, and reporting the mean like
count of those neighbours as the generated comment's "estimated engagement". A model
fine-tuned on the top-liked decile reportedly scores ~3x a model trained on random
comments.

The concern is that the metric is satisfied by construction: train on top-liked text and
your output lands near top-liked text in embedding space, so the neighbours are
high-liked whether or not a human would ever have liked the output.

This module tests that **without generating anything**. Two groups of *real* held-out
comments stand in for the two models:

- ``top_decile`` — real comments in the top 10% of within-video like rank, i.e. an
  idealised version of what an SFT-on-top-decile model is trying to imitate.
- ``random`` — real comments drawn uniformly, i.e. the baseline model's target.

If the estimator reports a large ratio between these groups, that ratio is reproducible
with no model involved. Any published ratio of similar size is therefore consistent with
the metric responding to text resemblance alone.

The second half asks the question the estimator structurally cannot: the retrieval score
has no access to when a comment was posted, so it is compared against real like outcomes
to see how much ordering skill it actually carries.

    python -m studies.comments.circularity --db ... --embeddings ...
"""

from __future__ import annotations

import argparse
import json

import numpy as np
from scipy.stats import spearmanr

from studies.comments.embed import load_cached
from studies.comments.features import channel_split, load


def retrieval_scores(
    queries: np.ndarray, reference: np.ndarray, ref_likes: np.ndarray, k: int, block: int = 2048
) -> np.ndarray:
    """Mean like count of each query's k nearest reference vectors (cosine).

    Vectors are L2-normalised at embed time, so a dot product is the cosine similarity
    and the top-k by dot product are the nearest neighbours.
    """
    out = np.empty(len(queries), dtype=np.float64)
    for start in range(0, len(queries), block):
        sims = queries[start : start + block] @ reference.T
        idx = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]
        out[start : start + block] = ref_likes[idx].mean(axis=1)
    return out


def run(db: str, embeddings: str, seed: int, n_ref: int, n_query: int) -> dict:
    data = load(db)
    emb = load_cached(embeddings, data.text)
    train_m, test_m = channel_split(data, seed=seed)
    rng = np.random.default_rng(seed)

    # Reference corpus: training channels only, exactly as an honest evaluator would.
    ref_idx = np.flatnonzero(train_m)
    ref_idx = rng.choice(ref_idx, size=min(n_ref, ref_idx.size), replace=False)
    reference, ref_likes = emb[ref_idx], data.likes[ref_idx].astype(float)

    test_idx = np.flatnonzero(test_m)

    # Two ways to define "the top 10% most liked", and the difference is the point.
    #
    # `absolute` ranks every comment against every other, so it is dominated by
    # comments from large videos — it selects on exposure as much as on quality, which
    # is how prior work picks its fine-tuning set.
    # `within_video` ranks each comment only against its own video's comments, which
    # removes the video-scale term. If the estimator's ratio survives the first and
    # vanishes under the second, the metric is tracking video scale, not likability.
    likes_test = data.likes[test_idx].astype(float)
    cut = np.quantile(likes_test, 0.9)
    pools = {
        "absolute": test_idx[likes_test >= cut],
        "within_video": test_idx[data.y[test_idx] >= 0.9],
    }
    rand = rng.choice(test_idx, size=min(n_query, test_idx.size), replace=False)

    result: dict = {"seed": seed, "n_reference": len(reference), "groups": {}}
    for label, pool in pools.items():
        top = rng.choice(pool, size=min(n_query, pool.size), replace=False)
        for k in (1, 5, 20):
            s_top = retrieval_scores(emb[top], reference, ref_likes, k)
            s_rand = retrieval_scores(emb[rand], reference, ref_likes, k)
            result["groups"][f"{label}|k={k}"] = {
                "top_decile_estimate": round(float(s_top.mean()), 3),
                "random_estimate": round(float(s_rand.mean()), 3),
                "ratio": round(float(s_top.mean() / max(1e-9, s_rand.mean())), 2),
                "n_top": int(top.size),
            }

    # Does the estimator actually order real comments by the likes they really got?
    sample = rng.choice(test_idx, size=min(n_query * 2, test_idx.size), replace=False)
    est = retrieval_scores(emb[sample], reference, ref_likes, 5)
    within = []
    for v in np.unique(data.video_idx[sample]):
        m = data.video_idx[sample] == v
        if m.sum() >= 20 and np.unique(data.y[sample][m]).size > 1:
            rho = spearmanr(data.y[sample][m], est[m]).statistic
            if not np.isnan(rho):
                within.append(rho)
    pooled = float(spearmanr(data.likes[sample], est).statistic)
    result["ordering_skill"] = {
        "pooled_spearman_vs_true_likes": round(pooled, 4),
        "within_video_spearman": round(float(np.mean(within)), 4),
        "n_videos": len(within),
    }
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/runs/comments/stratified.sqlite")
    p.add_argument("--embeddings", default="data/runs/comments/embeddings-minilm.npz")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--n-ref", type=int, default=150_000)
    p.add_argument("--n-query", type=int, default=20_000)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    out = run(a.db, a.embeddings, a.seed, a.n_ref, a.n_query)
    print(json.dumps(out, indent=2))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
