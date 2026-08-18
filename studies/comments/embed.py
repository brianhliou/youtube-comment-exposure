"""Cache sentence embeddings for the corpus — rung 4's input.

Embeddings are computed once and stored as a plain array whose row order is defined by
``features.load``. That alignment is the whole risk here: a silently misaligned
embedding matrix would pair every comment with someone else's vector and produce a
confidently wrong "text adds nothing" result. The cache therefore stores the row count
and a digest of the text it embedded, and the loader refuses a file that does not match
the corpus it is being used with.

    python -m studies.comments.embed --db data/runs/comments/stratified.sqlite \\
        --out data/runs/comments/embeddings-minilm.npz
"""

from __future__ import annotations

import argparse
import hashlib

import numpy as np

from studies.comments.features import load

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def text_digest(texts: list[str]) -> str:
    """Cheap fingerprint of the exact text sequence that was embedded."""
    h = hashlib.sha256()
    h.update(str(len(texts)).encode())
    for i in (0, len(texts) // 2, -1):
        h.update(texts[i].encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def build(db: str, out: str, batch_size: int, device: str | None) -> None:
    from sentence_transformers import SentenceTransformer

    data = load(db)
    print(f"embedding {len(data.text):,} comments with {MODEL}", flush=True)
    model = SentenceTransformer(MODEL, device=device)
    vectors = model.encode(
        data.text,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float16)
    np.savez_compressed(
        out, vectors=vectors, n=len(data.text), digest=text_digest(data.text), model=MODEL
    )
    print(f"wrote {out}  shape={vectors.shape}", flush=True)


def load_cached(path: str, texts: list[str]) -> np.ndarray:
    """Load the cache, refusing it unless it matches ``texts`` exactly."""
    blob = np.load(path, allow_pickle=False)
    vectors, n, digest = blob["vectors"], int(blob["n"]), str(blob["digest"])
    if n != len(texts) or digest != text_digest(texts):
        raise ValueError(
            f"embedding cache does not match this corpus (cached n={n}, digest={digest}; "
            f"corpus n={len(texts)}, digest={text_digest(texts)}) — re-run embed.py"
        )
    return vectors.astype(np.float32)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/runs/comments/stratified.sqlite")
    p.add_argument("--out", default="data/runs/comments/embeddings-minilm.npz")
    p.add_argument("--batch-size", type=int, default=512)
    p.add_argument("--device", default=None, help="mps / cpu / cuda; default auto")
    a = p.parse_args(argv)
    build(a.db, a.out, a.batch_size, a.device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
