"""Minimal YouTube Data API v3 client — stdlib only, quota-aware, key-safe.

Three properties this client exists to guarantee:

- **The API key never reaches a log, an exception, or a traceback.** The key travels
  as a query parameter, which makes the request URL secret-bearing; nothing here ever
  puts a URL into a message. Errors carry the endpoint name and the non-secret params.
- **Quota is charged on success.** A call that fails before returning a body has not
  billed the project, so the caller must not decrement its budget for it. The one
  exception is a quota error, which ends the quota day outright.
- **Transient failures retry; permanent ones don't.** 5xx and socket errors back off.
  ``commentsDisabled`` is a durable fact about a video, not a failure to retry.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_ROOT = "https://www.googleapis.com/youtube/v3"

# The quota day is over. Not retryable — stop until the Pacific-midnight reset.
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})

# Per-second/per-user throttling. Retryable, but back off harder than a 5xx.
THROTTLE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "backendError"})

# Durable facts about the resource. Record the reason and move on.
TERMINAL_REASONS = frozenset(
    {
        "commentsDisabled",
        "videoNotFound",
        "playlistNotFound",
        "channelNotFound",
        "channelClosed",
        "channelSuspended",
        "forbidden",
        "processingFailure",
    }
)


class YouTubeError(Exception):
    """Base for API failures. Never carries a URL — the URL holds the key."""


class QuotaExceeded(YouTubeError):
    """The project's daily quota is spent. Stop until the next Pacific day."""


class ResourceUnavailable(YouTubeError):
    """A durable fact about the resource (comments disabled, video gone, ...)."""

    def __init__(self, reason: str, endpoint: str) -> None:
        super().__init__(f"{endpoint}: {reason}")
        self.reason = reason


class TransientError(YouTubeError):
    """Retryable failure that outlived the retry budget."""


def _redact(params: dict[str, Any]) -> dict[str, Any]:
    """Params safe to put in a message — everything except the key."""
    return {k: v for k, v in params.items() if k != "key"}


def _parse_error(body: bytes) -> tuple[str, str]:
    """Extract ``(reason, message)`` from an API error body. Best effort."""
    try:
        payload = json.loads(body.decode("utf-8"))
        err = payload.get("error", {})
        errors = err.get("errors") or [{}]
        return str(errors[0].get("reason", "")), str(err.get("message", ""))
    except (ValueError, KeyError, IndexError, UnicodeDecodeError):
        return "", ""


class YouTubeClient:
    """One API key, one retry policy. Callers own quota accounting.

    ``units_spent`` counts only calls that returned a body, so a caller can decrement
    a durable ledger by exactly what the project was billed.
    """

    def __init__(
        self,
        api_key: str,
        *,
        max_retries: int = 4,
        timeout: float = 30.0,
        base_backoff: float = 1.0,
        sleeper: Any = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is empty — set YOUTUBE_API_KEY in the environment")
        self._api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout
        self.base_backoff = base_backoff
        self._sleep = sleeper
        self._rng = rng or random.Random()
        self.units_spent = 0

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET one page from ``endpoint``. Returns the decoded body.

        Raises :class:`QuotaExceeded`, :class:`ResourceUnavailable`, or
        :class:`TransientError`. Increments ``units_spent`` only on success.
        """
        query = {k: v for k, v in params.items() if v is not None}
        url = f"{API_ROOT}/{endpoint}?" + urllib.parse.urlencode({**query, "key": self._api_key})

        last_reason = ""
        for attempt in range(self.max_retries + 1):
            try:
                # nosec: URL is constructed above from a fixed API root.
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:  # noqa: S310
                    body = resp.read()
                self.units_spent += 1
                return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                reason, _message = _parse_error(exc.read())
                last_reason = reason or f"http_{exc.code}"
                if reason in QUOTA_REASONS:
                    raise QuotaExceeded(f"{endpoint}: {reason}") from None
                client_error = 400 <= exc.code < 500 and reason not in THROTTLE_REASONS
                if reason in TERMINAL_REASONS or client_error:
                    raise ResourceUnavailable(last_reason, endpoint) from None
                delay_scale = 4.0 if reason in THROTTLE_REASONS else 1.0
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                # Deliberately not interpolating `exc` for URLError — its message can
                # echo the request URL, which carries the key.
                last_reason = type(exc).__name__
                delay_scale = 1.0

            if attempt == self.max_retries:
                break
            backoff = self.base_backoff * (2**attempt) * delay_scale
            self._sleep(backoff + self._rng.uniform(0, self.base_backoff))

        raise TransientError(
            f"{endpoint} failed after {self.max_retries + 1} attempts "
            f"({last_reason}); params={_redact(query)}"
        )

    def paginate(
        self, endpoint: str, params: dict[str, Any], *, page_token: str | None = None
    ) -> Any:
        """Yield ``(body, next_page_token)`` per page, starting at ``page_token``.

        The caller drives the loop so it can checkpoint and re-check quota between
        pages; the token is yielded rather than followed automatically.
        """
        token = page_token
        while True:
            body = self.get(endpoint, {**params, "pageToken": token})
            token = body.get("nextPageToken")
            yield body, token
            if not token:
                return
