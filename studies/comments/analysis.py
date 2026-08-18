"""The gate: does text order comments better than exposure alone?

Runs the predictor ladder from the study README and reports the one number the whole
project turns on — the *incremental* ordering skill text buys over an exposure-only
baseline, measured on channels never seen in training.

Two metrics, because 82% of comments have zero likes:

- **Within-video Spearman** — the pre-registered primary (post-amendment). Honest but
  heavily tied, since most of the target is a single mass at zero.
- **Within-video AUC of P(likes >= 1)** — the hurdle's first stage, which is where most
  of the orderable signal actually lives and which ties do not distort.

Both are computed per video and averaged, with a bootstrap CI over *channels* so the
interval reflects transfer to a new audience rather than more comments from known ones.

    python -m studies.comments.analysis --db data/runs/comments/stratified.sqlite
"""

from __future__ import annotations

import argparse
import json

import numpy as np
from scipy import sparse
from scipy.stats import spearmanr
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from studies.comments.features import Dataset, channel_split, load


def video_spearman(y: np.ndarray, pred: np.ndarray, video_idx: np.ndarray) -> np.ndarray:
    """Spearman per video. Videos with a constant target contribute nothing."""
    out = []
    for v in np.unique(video_idx):
        m = video_idx == v
        if m.sum() < 20 or np.unique(y[m]).size < 2:
            continue
        rho = spearmanr(y[m], pred[m]).statistic
        if not np.isnan(rho):
            out.append(rho)
    return np.array(out)


def video_auc(likes: np.ndarray, pred: np.ndarray, video_idx: np.ndarray) -> np.ndarray:
    """Per-video AUC for 'received at least one like', via the rank identity."""
    out = []
    for v in np.unique(video_idx):
        m = video_idx == v
        label = likes[m] >= 1
        n_pos, n_neg = int(label.sum()), int((~label).sum())
        if n_pos < 5 or n_neg < 5:
            continue
        r = pred[m].argsort().argsort().astype(float) + 1
        out.append((r[label].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))
    return np.array(out)


def bootstrap_ci(
    per_video: np.ndarray, channels: np.ndarray, n_boot: int = 400, seed: int = 0
) -> tuple[float, float]:
    """Percentile CI resampling *channels*, the unit that has to generalise."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(channels)
    means = []
    for _ in range(n_boot):
        picked = rng.choice(uniq, size=uniq.size, replace=True)
        vals = np.concatenate([per_video[channels == c] for c in picked])
        if vals.size:
            means.append(vals.mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate(name: str, pred: np.ndarray, test: Dataset) -> dict:
    vids = np.array(
        [v for v in np.unique(test.video_idx)
         if (test.video_idx == v).sum() >= 20 and np.unique(test.y[test.video_idx == v]).size >= 2]
    )
    rho = video_spearman(test.y, pred, test.video_idx)
    auc = video_auc(test.likes, pred, test.video_idx)
    ch_rho = np.array([test.channel[test.video_idx == v][0] for v in vids])
    vids_auc = np.array(
        [v for v in np.unique(test.video_idx)
         if ((test.likes[test.video_idx == v] >= 1).sum() >= 5
             and ((test.likes[test.video_idx == v] < 1).sum() >= 5))]
    )
    ch_auc = np.array([test.channel[test.video_idx == v][0] for v in vids_auc])
    lo, hi = bootstrap_ci(rho, ch_rho)
    alo, ahi = bootstrap_ci(auc, ch_auc)
    return {
        "rung": name,
        "spearman": round(float(rho.mean()), 4),
        "spearman_ci": [round(lo, 4), round(hi, 4)],
        "auc": round(float(auc.mean()), 4),
        "auc_ci": [round(alo, 4), round(ahi, 4)],
        "n_videos": int(rho.size),
        # Per-video arrays kept for the paired comparison below. The video filters
        # depend only on the target, never on a prediction, so these stay aligned
        # across rungs and can be differenced element-wise.
        "_rho": rho, "_ch_rho": ch_rho, "_auc": auc, "_ch_auc": ch_auc,
    }


def paired_delta_ci(
    a: np.ndarray, b: np.ndarray, channels: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> dict:
    """Bootstrap CI for mean(a - b) over the same videos, resampling channels.

    Comparing two rungs by whether their marginal CIs overlap is the wrong test: the
    rungs score identical videos, so most of the spread is shared between them and
    cancels in the difference. A paired interval can exclude zero while the marginals
    overlap almost completely.
    """
    delta = a - b
    rng = np.random.default_rng(seed)
    uniq = np.unique(channels)
    means = []
    for _ in range(n_boot):
        picked = rng.choice(uniq, size=uniq.size, replace=True)
        vals = np.concatenate([delta[channels == c] for c in picked])
        if vals.size:
            means.append(vals.mean())
    means = np.array(means)
    return {
        "delta": round(float(delta.mean()), 4),
        "ci": [round(float(np.percentile(means, 2.5)), 4),
               round(float(np.percentile(means, 97.5)), 4)],
        "p_gt_0": round(float((means > 0).mean()), 4),
    }


ALPHAS = (1.0, 10.0, 100.0, 1000.0)


def fit_tuned(
    x_fit: sparse.csr_matrix, y_fit: np.ndarray,
    x_val: sparse.csr_matrix, val: Dataset,
    x_train: sparse.csr_matrix, y_train: np.ndarray,
) -> tuple[Ridge, float]:
    """Pick alpha on held-out *validation channels*, then refit on all training data.

    Every rung goes through this same function with the same estimator. A ladder that
    swaps optimizers between rungs is not an ablation: it confounds the feature change
    with the fitting change, and can make a superset model score *below* its own
    subset — which is diagnostic of a fitting failure, never of negative information.
    """
    best_alpha, best_score = ALPHAS[0], -np.inf
    for alpha in ALPHAS:
        model = Ridge(alpha=alpha, solver="sparse_cg", tol=1e-4)
        model.fit(x_fit, y_fit)
        score = float(video_auc(val.likes, model.predict(x_val), val.video_idx).mean())
        if score > best_score:
            best_alpha, best_score = alpha, score
    final = Ridge(alpha=best_alpha, solver="sparse_cg", tol=1e-4)
    final.fit(x_train, y_train)
    return final, best_alpha


def run(db: str, max_features: int, seed: int, embeddings: str | None = None) -> dict:
    print("loading corpus...", flush=True)
    data = load(db)
    emb = None
    if embeddings:
        from studies.comments.embed import load_cached

        emb = load_cached(embeddings, data.text)
        print(f"embeddings {emb.shape} loaded and verified against corpus", flush=True)
    train_m, test_m = channel_split(data, seed=seed)
    train, test = data.subset(train_m), data.subset(test_m)
    # A second channel-level split *inside* train supplies the alpha-selection set, so
    # the test channels are never touched during model selection.
    fit_m, val_m = channel_split(train, n_test=5, seed=seed + 100)
    fit, val = train.subset(fit_m), train.subset(val_m)
    print(
        f"train {train.y.size:,} / {len(set(train.channel))} ch "
        f"(fit {fit.y.size:,} / val {val.y.size:,}) | "
        f"test {test.y.size:,} / {len(set(test.channel))} ch",
        flush=True,
    )

    # Shared scaler so every rung sees the dense block on identical footing.
    scaler = StandardScaler().fit(np.hstack([train.x, train.x_len]))

    def dense(d: Dataset, with_length: bool) -> sparse.csr_matrix:
        block = scaler.transform(np.hstack([d.x, d.x_len]))
        if not with_length:
            block = block[:, : train.x.shape[1]]
        return sparse.csr_matrix(block)

    print("fitting tf-idf...", flush=True)
    vec = TfidfVectorizer(
        max_features=max_features, min_df=5, sublinear_tf=True,
        strip_accents="unicode", lowercase=True, ngram_range=(1, 2),
    )
    tf_fit = vec.fit_transform(fit.text)
    tf_val = vec.transform(val.text)
    tf_train = vec.transform(train.text)
    tf_test = vec.transform(test.text)

    rungs = {
        "exposure": (
            dense(fit, False), dense(val, False), dense(train, False), dense(test, False),
        ),
        "exposure+length": (
            dense(fit, True), dense(val, True), dense(train, True), dense(test, True),
        ),
        "exposure+length+tfidf": (
            sparse.hstack([dense(fit, True), tf_fit]).tocsr(),
            sparse.hstack([dense(val, True), tf_val]).tocsr(),
            sparse.hstack([dense(train, True), tf_train]).tocsr(),
            sparse.hstack([dense(test, True), tf_test]).tocsr(),
        ),
    }

    if emb is not None:
        # Index the cached matrix with the same masks used to slice the Dataset, in the
        # same order, so a row's vector stays with its own comment.
        #
        # Stays a dense ndarray on purpose. Wrapping a fully-dense block in csr_matrix
        # stores every value plus a column index for it — several times the memory of
        # the array it came from, which is enough to run a 36GB machine out of RAM.
        e_train, e_test = emb[train_m], emb[test_m]
        e_fit, e_val = e_train[fit_m], e_train[val_m]

        def with_embeddings(d: Dataset, e: np.ndarray) -> np.ndarray:
            block = scaler.transform(np.hstack([d.x, d.x_len])).astype(np.float32)
            return np.hstack([block, e])

        rungs["exposure+length+embed"] = (
            with_embeddings(fit, e_fit),
            with_embeddings(val, e_val),
            with_embeddings(train, e_train),
            with_embeddings(test, e_test),
        )
        del emb

    results = []
    for name, (x_fit, x_val, x_train, x_test) in rungs.items():
        model, alpha = fit_tuned(x_fit, fit.y, x_val, val, x_train, train.y)
        row = evaluate(name, model.predict(x_test), test)
        row["alpha"] = alpha
        row["n_features"] = x_train.shape[1]
        results.append(row)
        print(json.dumps({k: v for k, v in row.items() if not k.startswith("_")}), flush=True)
        if row["spearman"] < results[0]["spearman"] - 0.01:
            print(f"  WARNING: {name} scores below the exposure-only rung despite "
                  f"containing its features — suspect a fitting failure, not a finding.",
                  flush=True)

    by_name = {r["rung"]: r for r in results}
    base = by_name["exposure+length"]
    gate: dict = {
        "baseline_spearman": base["spearman"],
        "baseline_auc": base["auc"],
        "kill_threshold": 0.02,
    }
    for name in ("exposure+length+tfidf", "exposure+length+embed"):
        if name not in by_name:
            continue
        text = by_name[name]
        key = name.rsplit("+", 1)[-1]
        gate[key] = {
            "spearman": paired_delta_ci(text["_rho"], base["_rho"], base["_ch_rho"], seed=seed),
            "auc": paired_delta_ci(text["_auc"], base["_auc"], base["_ch_auc"], seed=seed),
            # Share of the above-chance ordering signal attributable to content.
            "text_share_of_auc": round(
                (text["auc"] - base["auc"]) / max(1e-9, text["auc"] - 0.5), 4
            ),
        }
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    return {"rungs": clean, "gate": gate}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/runs/comments/stratified.sqlite")
    p.add_argument("--max-features", type=int, default=200_000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default=None)
    p.add_argument("--embeddings", default=None, help="path to a cached embedding npz")
    a = p.parse_args(argv)
    out = run(a.db, a.max_features, a.seed, a.embeddings)
    print("\n=== GATE ===")
    print(json.dumps(out["gate"], indent=2))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
