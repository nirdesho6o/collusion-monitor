"""Retry LLM requests that the provider rejects for rate limiting (HTTP 429)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)


def _retry_delay_seconds(exc: Exception, default: float) -> float:
    """Use the server-suggested delay (e.g. Gemini's RetryInfo) when present."""
    match = _RETRY_DELAY_RE.search(str(exc))
    return float(match.group(1)) + 1.0 if match else default


def retry_generate_on_429(client: Any, *, max_retries: int = 8, default_delay: float = 60.0) -> Any:
    """Wrap ``client.generate_response`` so 429 errors resend the identical request.

    Free-tier Gemini keys are limited to a few requests per minute, which a single
    multi-agent run exceeds. Retrying the same request does not change the conversation.
    """
    generate = client.generate_response

    def generate_with_retry(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(max_retries + 1):
            try:
                return generate(*args, **kwargs)
            except Exception as exc:
                if getattr(exc, "code", None) != 429 or attempt == max_retries:
                    raise
                delay = _retry_delay_seconds(exc, default_delay)
                logger.warning("Rate limited (429); retry %s/%s in %.0fs", attempt + 1, max_retries, delay)
                time.sleep(delay)

    client.generate_response = generate_with_retry
    return client
