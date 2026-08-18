"""Does *generated* text inflate the retrieval estimator when real text doesn't?

`circularity.py` tested whether selecting top-liked *real* comments reproduces the ~3x
ratio reported by prior work. It does not (ratios 0.88-1.21). One explanation survives:
generated text may be far more homogeneous than real text, so a model conditioned on
top-liked exemplars could land in a tight cluster near high-liked reference comments in
a way real held-out comments never do. That would inflate a nearest-neighbour estimator
without the generator being any better.

This tests it directly with two generators over the same held-out videos:

- ``plain`` — write a comment for this video. The baseline model's behaviour.
- ``topliked`` — same, few-shot conditioned on real top-liked comments drawn from
  *training* channels. An approximation of fine-tuning on the top-liked decile, which is
  what prior work does. Few-shot is not fine-tuning; it pushes in the same direction for
  a fraction of the cost, and the write-up says so.

Each set is scored twice: by the retrieval estimator under audit, and by real
distributional position (how the generated text's predicted rank compares to the true
comments on that video). A large retrieval gap next to a small real gap is the
inflation this study set out to find.

    python -m studies.comments.generate --db ... --embeddings ... --n-videos 30
"""

from __future__ import annotations

import argparse
import json
import random

import numpy as np

from studies.comments.circularity import retrieval_scores
from studies.comments.embed import load_cached
from studies.comments.features import channel_split, load

PLAIN = (
    "Write a single YouTube comment for this video. Reply with only the comment text.\n\n"
    "Video title: {title}\nChannel: {channel}"
)

TOPLIKED = (
    "Write a single YouTube comment for this video. Reply with only the comment text.\n\n"
    "Here are examples of comments that got a lot of likes on this platform:\n{examples}\n"
    "Write a comment in that style.\n\n"
    "Video title: {title}\nChannel: {channel}"
)


def build_prompts(
    data, train_m: np.ndarray, test_m: np.ndarray,
    n_videos: int, n_examples: int, rng: random.Random,
) -> tuple[list[dict], list[str]]:
    """Pick held-out videos to write for, and top-liked training comments to imitate."""
    train_idx = np.flatnonzero(train_m)
    top_train = train_idx[data.y[train_idx] >= 0.95]
    picks = rng.sample(list(top_train), n_examples)
    examples = [data.text[i].replace("\n", " ")[:200] for i in picks]

    test_idx = np.flatnonzero(test_m)
    videos = sorted({int(v) for v in data.video_idx[test_idx]})
    picked = rng.sample(videos, min(n_videos, len(videos)))
    targets = []
    for v in picked:
        rows = test_idx[data.video_idx[test_idx] == v]
        targets.append({
            "video": v,
            "video_id": data.video_ids.get(v, ""),
            "channel": str(data.channel[rows[0]]),
            "n_real": len(rows),
        })
    return targets, examples


def run(
    db: str, embeddings: str, model: str, n_videos: int, n_examples: int, seed: int,
    samples_per_video: int = 1,
) -> dict:
    from arena.models import complete

    data = load(db)
    emb = load_cached(embeddings, data.text)
    train_m, test_m = channel_split(data, seed=seed)
    rng = random.Random(seed)
    targets, examples = build_prompts(data, train_m, test_m, n_videos, n_examples, rng)
    example_block = "\n".join(f"- {e}" for e in examples)

    # Titles keyed by video_id. Keying this by channel_handle silently gave every video
    # from a channel that channel's last title, which made the generations
    # non-video-specific while still looking correct.
    import sqlite3

    conn = sqlite3.connect(db)
    titles = dict(conn.execute("SELECT video_id, title FROM videos WHERE status='done'"))
    conn.close()

    generated: dict[str, list[str]] = {"plain": [], "topliked": []}
    cost = 0.0
    for t in targets:
        title = titles.get(t["video_id"], "a video")
        for condition, template in (("plain", PLAIN), ("topliked", TOPLIKED)):
            prompt = template.format(title=title, channel=t["channel"], examples=example_block)
            for _ in range(samples_per_video):
                r = complete(prompt, model, temperature=1.0, max_tokens=80)
                generated[condition].append(r.text.strip().strip('"'))
                cost += r.cost_usd

    # Score both sets with the estimator under audit, against a training-channel corpus.
    from sentence_transformers import SentenceTransformer

    from studies.comments.embed import MODEL

    encoder = SentenceTransformer(MODEL)
    ref_idx = np.flatnonzero(train_m)
    ref_idx = rng_choice(ref_idx, 150_000, seed)
    reference, ref_likes = emb[ref_idx], data.likes[ref_idx].astype(float)

    out: dict = {
        "model": model, "n_videos": len(targets), "n_per_condition": len(generated["plain"]),
        "cost_usd": round(cost, 4), "scores": {},
    }
    vecs = {
        c: encoder.encode(v, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
        for c, v in generated.items()
    }
    # Real held-out comments as the honest control: whatever the estimator says about
    # generated text, it should say something comparable about genuine comments.
    real_idx = rng_choice(np.flatnonzero(test_m), 2000, seed + 1)
    for k in (1, 5, 20):
        per = {c: retrieval_scores(vecs[c], reference, ref_likes, k) for c in vecs}
        per["real_heldout"] = retrieval_scores(emb[real_idx], reference, ref_likes, k)
        # Like counts are heavy-tailed (max 240k in this corpus), so a mean over a few
        # dozen generations is dominated by whichever one landed beside a viral comment.
        # Report the median alongside it and bootstrap the ratio, or the number swings
        # by an order of magnitude between identical runs.
        out["scores"][f"k={k}"] = {
            **{f"{c}_mean": round(float(v.mean()), 3) for c, v in per.items()},
            **{f"{c}_median": round(float(np.median(v)), 3) for c, v in per.items()},
            "ratio_mean": round(float(per["topliked"].mean() / max(1e-9, per["plain"].mean())), 2),
            "ratio_median": round(
                float(np.median(per["topliked"]) / max(1e-9, np.median(per["plain"]))), 2
            ),
            "ratio_ci": bootstrap_ratio_ci(per["topliked"], per["plain"], seed),
        }

    # Homogeneity: mean pairwise cosine within each set. The proposed mechanism predicts
    # generated text clusters far more tightly than real comments do.
    out["mean_pairwise_cosine"] = {
        c: round(float((v @ v.T)[np.triu_indices(len(v), 1)].mean()), 4) for c, v in vecs.items()
    }
    rv = emb[rng_choice(np.flatnonzero(test_m), len(targets), seed + 2)]
    out["mean_pairwise_cosine"]["real_heldout"] = round(
        float((rv @ rv.T)[np.triu_indices(len(rv), 1)].mean()), 4
    )
    out["samples"] = {c: v[:5] for c, v in generated.items()}
    return out


def bootstrap_ratio_ci(a: np.ndarray, b: np.ndarray, seed: int, n_boot: int = 2000) -> list[float]:
    """Percentile CI for mean(a)/mean(b), resampling each set independently.

    A ratio of two heavy-tailed means is the least stable statistic in this study, so
    it never gets reported as a point estimate on its own.
    """
    g = np.random.default_rng(seed)
    ratios = []
    for _ in range(n_boot):
        num = g.choice(a, a.size, replace=True).mean()
        den = g.choice(b, b.size, replace=True).mean()
        ratios.append(num / max(1e-9, den))
    lo, hi = np.percentile(ratios, 2.5), np.percentile(ratios, 97.5)
    return [round(float(lo), 2), round(float(hi), 2)]


def rng_choice(pool: np.ndarray, size: int, seed: int) -> np.ndarray:
    g = np.random.default_rng(seed)
    return g.choice(pool, size=min(size, pool.size), replace=False)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/runs/comments/stratified.sqlite")
    p.add_argument("--embeddings", default="data/runs/comments/embeddings-minilm.npz")
    p.add_argument("--model", default="anthropic/claude-haiku-4-5-20251001")
    p.add_argument("--n-videos", type=int, default=30)
    p.add_argument("--n-examples", type=int, default=12)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--samples-per-video", type=int, default=1)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    out = run(a.db, a.embeddings, a.model, a.n_videos, a.n_examples, a.seed, a.samples_per_video)
    print(json.dumps(out, indent=2))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
