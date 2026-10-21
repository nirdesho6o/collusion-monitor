"""Pace, count and retry Gemini API requests.

Free-tier Gemini keys are limited per minute and per day. A single multi-agent run exceeds the
per-minute limit, so requests are paced, rate-limited (HTTP 429) requests are resent unchanged, and
every request attempt is counted in a daily ledger with a hard cap.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)
# Without a timeout a request on a dead connection (e.g. after laptop suspend) blocks forever.
REQUEST_TIMEOUT_MS = 120_000
# Gemini API daily quotas reset at midnight Pacific time.
_QUOTA_TZ = ZoneInfo("America/Los_Angeles")


class RequestBudgetExceeded(RuntimeError):
    """Raised instead of sending a request once the daily cap is reached."""


def _retry_delay_seconds(exc: Exception, default: float) -> float:
    """Use the server-suggested delay (e.g. Gemini's RetryInfo) when present."""
    match = _RETRY_DELAY_RE.search(str(exc))
    return float(match.group(1)) + 1.0 if match else default


class RequestPacer:
    """Process-wide pacing (max requests/minute) plus a per-day request ledger with a hard cap.

    Every attempt is counted, including ones rejected with 429, because the provider may count them.
    """

    def __init__(self, *, max_rpm: float, daily_cap: Optional[int], ledger_path: Optional[str]):
        self.min_interval = 60.0 / float(max_rpm)
        self.daily_cap = daily_cap
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.exhausted = False
        self._lock = threading.Lock()
        self._last_request = 0.0

    @staticmethod
    def today() -> str:
        return datetime.now(_QUOTA_TZ).date().isoformat()

    def _read(self) -> Dict[str, int]:
        if self.ledger_path and self.ledger_path.exists():
            return json.loads(self.ledger_path.read_text())
        return {}

    def used_today(self) -> int:
        return int(self._read().get(self.today(), 0))

    def acquire(self) -> None:
        """Block until a request may be sent, then count it; raise if the daily cap is reached."""
        with self._lock:
            ledger = self._read()
            day = self.today()
            used = int(ledger.get(day, 0))
            if self.daily_cap is not None and used >= self.daily_cap:
                self.exhausted = True
                raise RequestBudgetExceeded(f"Daily request cap reached ({used}/{self.daily_cap}).")
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            ledger[day] = used + 1
            if self.ledger_path:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                self.ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True))


_PACERS: Dict[str, RequestPacer] = {}


def get_pacer(pacing_cfg: Optional[Dict[str, Any]]) -> Optional[RequestPacer]:
    """Shared pacer for `experiment.request_pacing` ({max_rpm, daily_cap, ledger_path}); None if unset."""
    if not pacing_cfg:
        return None
    key = json.dumps(pacing_cfg, sort_keys=True)
    if key not in _PACERS:
        _PACERS[key] = RequestPacer(
            max_rpm=float(pacing_cfg["max_rpm"]),
            daily_cap=pacing_cfg.get("daily_cap"),
            ledger_path=pacing_cfg.get("ledger_path"),
        )
    return _PACERS[key]


def retry_generate_on_429(
    client: Any,
    *,
    pacer: Optional[RequestPacer] = None,
    max_retries: int = 6,
    default_delay: float = 30.0,
) -> Any:
    """Wrap each Gemini API request: pace/count it, add a timeout, and resend it unchanged on 429/503/timeouts.

    Wraps the SDK's `models.generate_content`, so internal second requests made by the client (e.g. its
    retry after MALFORMED_FUNCTION_CALL) are paced and counted too. Retrying does not change the conversation.
    """
    models = client.client.models
    generate = models.generate_content

    def generate_with_retry(*args: Any, **kwargs: Any) -> Any:
        config = kwargs.get("config")
        if config is not None and getattr(config, "http_options", None) is None:
            from google.genai import types

            kwargs["config"] = config.model_copy(
                update={"http_options": types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)}
            )
        for attempt in range(max_retries + 1):
            if pacer is not None:
                pacer.acquire()
            try:
                return generate(*args, **kwargs)
            except httpx.TransportError as exc:
                # Timeouts and dropped connections: transient, resend the identical request.
                if attempt == max_retries:
                    raise
                delay = min(default_delay * 2 ** attempt, 300.0)
                logger.warning("%s; retry %s/%s in %.0fs", type(exc).__name__, attempt + 1, max_retries, delay)
                time.sleep(delay)
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code not in (429, 503) or attempt == max_retries:
                    raise
                # 429: server-suggested delay. 503 ("high demand"): transient overload, back off.
                delay = (
                    _retry_delay_seconds(exc, default_delay)
                    if code == 429
                    else min(default_delay * 2 ** attempt, 300.0)
                )
                logger.warning("HTTP %s; retry %s/%s in %.0fs", code, attempt + 1, max_retries, delay)
                time.sleep(delay)

    models.generate_content = generate_with_retry
    return client
