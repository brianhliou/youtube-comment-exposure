"""Reproducibility manifest serializer — shared infrastructure, not per-study.

Every study's final harness requirement is the same: write a committed JSON manifest
(seeds, model version strings, temperatures, prompt-template hashes, condition params,
code version) sufficient to recreate any trial without re-querying the model. Studies
add their own fields to ``config``; the serializer stamps the shared envelope and
writes canonical JSON. Commit the manifest + summary artifacts, never raw run dumps
(``data/runs`` and ``data/cache`` are gitignored).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena import __version__


def hash_text(text: str) -> str:
    """SHA-256 hex digest of a prompt/template string (12 chars is plenty for an id)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_manifest(
    study: str,
    config: dict[str, Any],
    *,
    created_at: str | None = None,
    prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble the canonical manifest envelope around a study's ``config``.

    ``prompts`` maps a prompt id → its raw text; each is replaced by its hash so the
    manifest pins the exact template without embedding it.
    """
    manifest: dict[str, Any] = {
        "arena_version": __version__,
        "study": study,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "config": config,
    }
    if prompts:
        manifest["prompt_hashes"] = {pid: hash_text(text) for pid, text in prompts.items()}
    return manifest


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    """Write a manifest as canonical (sorted-key, indented) JSON. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path
