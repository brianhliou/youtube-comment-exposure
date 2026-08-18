"""Unified LLM adapter: one interface over providers, with cost/token accounting.

Wraps litellm behind a single call. Cost accounting is mandatory: every call records
input/output tokens and dollar cost (from litellm's per-model pricing) so they land
in ``Trial.meta``. ``complete_n`` is the fan-out path — N independent draws of the
*same* prompt (Crowds self-consistency, RPS replicates). A stateful repeated-game
loop (RPS) is a sequence of ``complete`` calls threading a growing ``messages`` list;
a stateless loop is independent ``complete`` calls with fresh context each round.

Secrets: API keys come from the environment ONLY (litellm reads them). Never read,
log, or print them. Importing this module must not require the ``llm`` extra — the
provider SDKs are imported lazily inside :func:`complete`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class LLMResult(BaseModel):
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _build_messages(
    prompt: str, system: str | None, messages: list[dict[str, str]] | None
) -> list[dict[str, str]]:
    if messages is not None:
        return messages
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _result_from_response(resp: Any, model: str, cost_usd: float | None = None) -> LLMResult:
    """Extract text/tokens/cost from a litellm response object (or a test double).

    Tolerates object- and dict-style usage so it can be unit-tested without litellm.
    When ``cost_usd`` is None, computes it via ``litellm.completion_cost`` if available,
    falling back to 0.0 (never raises just to attach a price).
    """
    choice = resp.choices[0]
    text = getattr(getattr(choice, "message", choice), "content", "") or ""
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")

    def _u(field: str) -> int:
        if usage is None:
            return 0
        if isinstance(usage, dict):
            return int(usage.get(field, 0))
        return int(getattr(usage, field, None) or 0)

    if cost_usd is None:
        try:
            import litellm

            cost_usd = float(litellm.completion_cost(completion_response=resp))
        except Exception:
            cost_usd = 0.0
    return LLMResult(
        text=text,
        model=model,
        input_tokens=_u("prompt_tokens"),
        output_tokens=_u("completion_tokens"),
        cost_usd=cost_usd,
    )


def complete(
    prompt: str,
    model: str,
    *,
    temperature: float = 1.0,
    max_tokens: int | None = None,
    system: str | None = None,
    messages: list[dict[str, str]] | None = None,
    seed: int | None = None,
    **kwargs: Any,
) -> LLMResult:
    """One completion with cost accounting. Requires the ``llm`` extra (lazy import)."""
    try:
        import litellm
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "arena.models.complete needs the 'llm' extra: pip install -e '.[llm]'"
        ) from e
    resp = litellm.completion(
        model=model,
        messages=_build_messages(prompt, system, messages),
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        num_retries=kwargs.pop("num_retries", 2),
        # drop params a model family doesn't support (e.g. temperature on Opus 4.7+),
        # so one adapter works across GPT/Claude/Gemini. Recorded per call regardless.
        drop_params=kwargs.pop("drop_params", True),
        **kwargs,
    )
    return _result_from_response(resp, model)


def complete_n(prompt: str, model: str, n: int, **kwargs: Any) -> list[LLMResult]:
    """N independent draws of the same prompt. The fan-out rung (n==1 is degenerate)."""
    return [complete(prompt, model, **kwargs) for _ in range(n)]
