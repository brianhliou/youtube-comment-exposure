"""Study B (V2.md): v1's pre-registered ladder, per language and pooled, on v2.sqlite.

Reuses v1's code unchanged for every pre-registered number: ``features`` (feature
construction, channel split), ``analysis.fit_tuned`` / ``evaluate`` / ``paired_delta_ci``
(same estimator, same alpha grid, same per-video AUC and Spearman, same channel
bootstrap). Two things are added, both because V2.md names an endpoint v1's driver never
computed:

- **Language filter.** ``load_lang`` is ``features.load`` with a channel-handle filter
  (language from ``config/panel-channels.json``); the row construction is copied line for
  line so a language subset is built exactly as the pooled corpus is.
- **Content-only rung** (H8): TF-IDF over the text alone, no exposure and no length
  features, through the same ``fit_tuned``.

Exploratory (not in the plan, labelled as such in every output): a character n-gram
TF-IDF for ja/zh, because v1's word tokenizer (``\\b\\w\\w+\\b``) treats an unspaced CJK
run as a single token, which handicaps the text rung and so biases the exposure share up
and the content-only AUC down in exactly the two languages the replication is about.

    python -m studies.comments.v2_bulk tfidf --lang zh --seeds 0
    python -m studies.comments.v2_bulk embed --lang zh         # cache MiniLM vectors
    python -m studies.comments.v2_bulk ladder-embed --lang zh --seeds 0 1 2 3 4
    python -m studies.comments.v2_bulk summary
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import StandardScaler

from studies.comments.analysis import evaluate, fit_tuned, paired_delta_ci
from studies.comments.features import (
    Dataset,
    _duration_seconds,
    _length_features,
    channel_split,
)

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data/runs/comments/v2.sqlite"
OUT = ROOT / "studies/comments/results/v2/bulk"
CHANNELS = ROOT / "studies/comments/config/panel-channels.json"
EMB_DIR = ROOT / "data/runs/comments"
LANGS = ("en", "ja", "zh")
MAX_FEATURES = 200_000  # v1 default


def lang_handles(lang: str) -> list[str] | None:
    chans = json.loads(CHANNELS.read_text())["channels"]
    if lang == "pooled":
        return [c["handle"] for c in chans if not c.get("marquee")]
    return [c["handle"] for c in chans if c["lang"] == lang and not c.get("marquee")]


def load_lang(db_path: str, handles: list[str], min_comments_per_video: int = 20) -> Dataset:
    """``features.load`` with ``WHERE v.channel_handle IN (handles)``; otherwise identical."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TEMP TABLE keep(h TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO keep VALUES (?)", [(h,) for h in handles])
    rows = conn.execute(
        """
        SELECT cm.video_id, cm.text, cm.like_count,
               (julianday(cm.published_at) - julianday(v.published_at)) * 24.0 AS age_h,
               v.view_count, v.comment_count, v.duration, v.channel_handle
        FROM comments cm JOIN videos v USING(video_id)
        WHERE v.status = 'done' AND cm.published_at IS NOT NULL
          AND v.channel_handle IN (SELECT h FROM temp.keep)
        ORDER BY cm.video_id, cm.published_at
        """
    ).fetchall()
    conn.close()

    by_video: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_video.setdefault(r["video_id"], []).append(r)
    del rows

    x, x_len, text, y, likes, video_idx, channel = [], [], [], [], [], [], []
    video_ids: dict[int, str] = {}
    for vi, (_vid, group) in enumerate(by_video.items()):
        n = len(group)
        if n < min_comments_per_video:
            continue
        lk = np.array([g["like_count"] for g in group], dtype=float)
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


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _splits(data: Dataset, seed: int):
    # Exactly analysis.run's split: 8 held-out test channels, 5 validation channels.
    train_m, test_m = channel_split(data, seed=seed)
    train, test = data.subset(train_m), data.subset(test_m)
    fit_m, val_m = channel_split(train, n_test=5, seed=seed + 100)
    fit, val = train.subset(fit_m), train.subset(val_m)
    return train_m, test_m, fit_m, val_m, train, test, fit, val


def _row(name: str, model_alpha, pred, test: Dataset, n_features: int) -> dict:
    row = evaluate(name, pred, test)
    row["alpha"], row["n_features"] = model_alpha, n_features
    return row


def _gate(results: list[dict], seed: int) -> dict:
    by = {r["rung"]: r for r in results}
    base = by["exposure+length"]
    expo = by["exposure"]
    gate: dict = {"baseline_spearman": base["spearman"], "baseline_auc": base["auc"]}
    for name in [r["rung"] for r in results if r["rung"].startswith("exposure+length+")]:
        text = by[name]
        key = name.split("+", 2)[-1]
        gate[key] = {
            "spearman": paired_delta_ci(text["_rho"], base["_rho"], base["_ch_rho"], seed=seed),
            "auc": paired_delta_ci(text["_auc"], base["_auc"], base["_ch_auc"], seed=seed),
            # v1's decomposition (paper, "Where the line goes"): shares of above-chance AUC.
            "text_share_of_auc": round((text["auc"] - base["auc"]) / max(1e-9, text["auc"] - 0.5), 4),
            "exposure_share_of_auc": round((expo["auc"] - 0.5) / max(1e-9, text["auc"] - 0.5), 4),
        }
    return gate


def _write(lang: str, seed: int, kind: str, results: list[dict], meta: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    path = OUT / f"{kind}-{lang}-seed{seed}.json"
    path.write_text(json.dumps({"lang": lang, "seed": seed, "meta": meta, "rungs": clean,
                                "gate": _gate(results, seed)}, indent=2))
    return path


def cmd_tfidf(args: argparse.Namespace) -> None:
    for lang in args.lang:
        _log(f"loading {lang}")
        data = load_lang(str(DB), lang_handles(lang))
        meta = {
            "comments": int(data.y.size), "videos": int(np.unique(data.video_idx).size),
            "channels": int(np.unique(data.channel).size),
        }
        _log(f"{lang}: {meta}")
        for seed in args.seeds:
            t0 = time.time()
            _, _, _, _, train, test, fit, val = _splits(data, seed)
            scaler = StandardScaler().fit(np.hstack([train.x, train.x_len]))

            def dense(d: Dataset, with_length: bool, scaler=scaler, train=train):
                block = scaler.transform(np.hstack([d.x, d.x_len]))
                if not with_length:
                    block = block[:, : train.x.shape[1]]
                return sparse.csr_matrix(block)

            results = []
            for name, wl in (("exposure", False), ("exposure+length", True)):
                xs = [dense(d, wl) for d in (fit, val, train, test)]
                model, alpha = fit_tuned(xs[0], fit.y, xs[1], val, xs[2], train.y)
                results.append(_row(name, alpha, model.predict(xs[3]), test, xs[2].shape[1]))
                _log(f"{lang} s{seed} {name} auc={results[-1]['auc']}")

            analyzers = [("tfidf", dict(ngram_range=(1, 2)), False)]
            if args.char and lang in ("ja", "zh"):
                analyzers.append(("chartfidf[exploratory]",
                                  dict(analyzer="char_wb", ngram_range=(1, 3)), True))
            for tag, kw, _expl in analyzers:
                vec = TfidfVectorizer(max_features=MAX_FEATURES, min_df=5, sublinear_tf=True,
                                      strip_accents="unicode", lowercase=True, **kw)
                tf = [vec.fit_transform(fit.text)] + [vec.transform(d.text) for d in (val, train, test)]
                # v1 rung: exposure + length + text
                xs = [sparse.hstack([dense(d, True), t]).tocsr()
                      for d, t in zip((fit, val, train, test), tf, strict=True)]
                model, alpha = fit_tuned(xs[0], fit.y, xs[1], val, xs[2], train.y)
                results.append(_row(f"exposure+length+{tag}", alpha, model.predict(xs[3]), test,
                                    xs[2].shape[1]))
                _log(f"{lang} s{seed} +{tag} auc={results[-1]['auc']}")
                del xs
                # content-only rung (H8): text alone
                model, alpha = fit_tuned(tf[0], fit.y, tf[1], val, tf[2], train.y)
                results.append(_row(f"content-only:{tag}", alpha, model.predict(tf[3]), test,
                                    tf[2].shape[1]))
                _log(f"{lang} s{seed} content-only:{tag} auc={results[-1]['auc']}")
                del tf, vec
                gc.collect()
            meta_s = dict(meta, seconds=round(time.time() - t0),
                          test_channels=sorted(set(test.channel.tolist())))
            _log(f"wrote {_write(lang, seed, 'tfidf', results, meta_s)}")
        del data
        gc.collect()


def _emb_path(lang: str) -> Path:
    return EMB_DIR / f"v2-embeddings-minilm-{lang}.npz"


def cmd_embed(args: argparse.Namespace) -> None:
    from sentence_transformers import SentenceTransformer

    from studies.comments.embed import MODEL, text_digest

    for lang in args.lang:
        data = load_lang(str(DB), lang_handles(lang))
        _log(f"embedding {len(data.text):,} {lang} comments with {MODEL}")
        model = SentenceTransformer(MODEL, device=args.device)
        chunk = 250_000
        parts = []
        for i in range(0, len(data.text), chunk):
            parts.append(model.encode(data.text[i:i + chunk], batch_size=512,
                                      show_progress_bar=False, convert_to_numpy=True,
                                      normalize_embeddings=True).astype(np.float16))
            _log(f"{lang} embedded {min(i + chunk, len(data.text)):,}/{len(data.text):,}")
        vectors = np.concatenate(parts)
        del parts
        np.savez(_emb_path(lang), vectors=vectors, n=len(data.text),
                 digest=text_digest(data.text), model=MODEL)
        _log(f"wrote {_emb_path(lang)} {vectors.shape}")
        del data, vectors
        gc.collect()


def _fit_tuned_lazy(make, fit: Dataset, val: Dataset, train: Dataset):
    """``analysis.fit_tuned`` with the same grid and estimator, building one dense
    matrix at a time (tune on fit/val, free them, then refit on train) so a 5M-row
    embedding block never has three copies alive at once."""
    from sklearn.linear_model import Ridge

    from studies.comments.analysis import ALPHAS, video_auc

    x_fit, x_val = make("fit"), make("val")
    best_alpha, best_score = ALPHAS[0], -np.inf
    for alpha in ALPHAS:
        model = Ridge(alpha=alpha, solver="sparse_cg", tol=1e-4)
        model.fit(x_fit, fit.y)
        score = float(video_auc(val.likes, model.predict(x_val), val.video_idx).mean())
        if score > best_score:
            best_alpha, best_score = alpha, score
    del x_fit, x_val
    gc.collect()
    x_train = make("train")
    final = Ridge(alpha=best_alpha, solver="sparse_cg", tol=1e-4)
    final.fit(x_train, train.y)
    nfeat = x_train.shape[1]
    del x_train
    gc.collect()
    return final, best_alpha, nfeat


def cmd_ladder_embed(args: argparse.Namespace) -> None:
    """Exposure, exposure+length, +MiniLM, MiniLM-only; matrices built one at a time."""
    from studies.comments.embed import text_digest

    for lang in args.lang:
        data = load_lang(str(DB), lang_handles(lang))
        blob = np.load(_emb_path(lang), allow_pickle=False)
        emb = blob["vectors"]  # float16, kept that way to halve memory
        if int(blob["n"]) != len(data.text) or str(blob["digest"]) != text_digest(data.text):
            raise ValueError(f"embedding cache for {lang} does not match the corpus")
        meta = {"comments": int(data.y.size), "videos": int(np.unique(data.video_idx).size),
                "channels": int(np.unique(data.channel).size)}
        for seed in args.seeds:
            t0 = time.time()
            train_m, test_m, fit_m, val_m, train, test, fit, val = _splits(data, seed)
            # Row indices into ``emb`` for each part, so no float16 copy is held per part.
            tr_idx = np.flatnonzero(train_m)
            idx = {"fit": tr_idx[fit_m], "val": tr_idx[val_m], "train": tr_idx,
                   "test": np.flatnonzero(test_m)}
            ds = {"fit": fit, "val": val, "train": train, "test": test}
            scaler = StandardScaler().fit(np.hstack([train.x, train.x_len]))

            def block(k: str, with_length: bool, with_emb: bool, only_emb: bool = False,
                      scaler=scaler, train=train, ds=ds, idx=idx, emb=emb):
                if only_emb:
                    return emb[idx[k]].astype(np.float32)
                b = scaler.transform(np.hstack([ds[k].x, ds[k].x_len])).astype(np.float32)
                if not with_length:
                    b = b[:, : train.x.shape[1]]
                return np.hstack([b, emb[idx[k]].astype(np.float32)]) if with_emb else b

            results = []
            for name, wl, we, oe in (("exposure", False, False, False),
                                     ("exposure+length", True, False, False),
                                     ("exposure+length+embed", True, True, False),
                                     ("content-only:embed", False, False, True)):
                model, alpha, nfeat = _fit_tuned_lazy(
                    lambda k, wl=wl, we=we, oe=oe: block(k, wl, we, oe), fit, val, train)
                results.append(_row(name, alpha, model.predict(block("test", wl, we, oe)), test,
                                    nfeat))
                _log(f"{lang} s{seed} {name} auc={results[-1]['auc']}")
                gc.collect()
            meta_s = dict(meta, seconds=round(time.time() - t0), encoder="all-MiniLM-L6-v2")
            _log(f"wrote {_write(lang, seed, 'embed', results, meta_s)}")
            gc.collect()
        del data, emb
        gc.collect()


def cmd_summary(_args: argparse.Namespace) -> None:
    files = sorted(OUT.glob("*.json"))
    table: dict[tuple[str, str], list[dict]] = {}
    for f in files:
        d = json.loads(f.read_text())
        kind = f.name.split("-")[0]
        table.setdefault((kind, d["lang"]), []).append(d)
    summary = {}
    for (kind, lang), runs in sorted(table.items()):
        rungs = {}
        for d in runs:
            for r in d["rungs"]:
                rungs.setdefault(r["rung"], []).append(r["auc"])
        shares = {}
        for d in runs:
            for k, g in d["gate"].items():
                if isinstance(g, dict):
                    shares.setdefault(k, {"exposure_share": [], "text_share": [],
                                          "d_auc": [], "d_spearman": []})
                    shares[k]["exposure_share"].append(g["exposure_share_of_auc"])
                    shares[k]["text_share"].append(g["text_share_of_auc"])
                    shares[k]["d_auc"].append(g["auc"]["delta"])
                    shares[k]["d_spearman"].append(g["spearman"]["delta"])
        summary[f"{kind}:{lang}"] = {
            "seeds": sorted(d["seed"] for d in runs),
            "meta": runs[0]["meta"] | {"seconds": None, "test_channels": None},
            "mean_auc": {k: round(float(np.mean(v)), 4) for k, v in rungs.items()},
            "decomposition": {
                k: {kk: [round(float(np.mean(vv)), 4), round(float(np.min(vv)), 4),
                         round(float(np.max(vv)), 4)] for kk, vv in s.items()}
                for k, s in shares.items()
            },
        }
    path = OUT.parent / "bulk-summary.json"
    path.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("tfidf", "embed", "ladder-embed"):
        s = sub.add_parser(name)
        s.add_argument("--lang", nargs="+", default=list(LANGS))
        s.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
        s.add_argument("--char", action="store_true", help="add the exploratory char n-gram rung")
        s.add_argument("--device", default=None)
    sub.add_parser("summary")
    a = p.parse_args(argv)
    {"tfidf": cmd_tfidf, "embed": cmd_embed, "ladder-embed": cmd_ladder_embed,
     "summary": cmd_summary}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
