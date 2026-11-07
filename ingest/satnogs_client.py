"""Client for pulling telemetry frames from the SatNOGS DB REST API."""

import logging
import time

import requests

from ingest.models import RawFrame

logger = logging.getLogger(__name__)

TELEMETRY_URL = "https://db.satnogs.org/api/telemetry/"
DEFAULT_BACKOFF_SECONDS = 30.0  # used when a 429 has no Retry-After header
MAX_BACKOFF_SECONDS = 120.0  # never sleep longer than this on one retry


class SatNOGSAuthError(Exception):
    """Raised when SatNOGS rejects our API token (HTTP 401 or 403)."""
    pass


class SatNOGSClient:
    """Fetches telemetry for one satellite, page by page.
    Handles the three things a raw requests.get() does not:
    * pagination (SatNOGS returns results in pages)
    * rate limiting (HTTP 429, wait and retry)
    * bad entried (skip them instead of crashing the whole run)
    """

    def __init__(self, token: str, max_retries: int = 3, timeout: float = 30.0):
        if not token:
            raise ValueError("A SatNOGS DB API token is required")
        self.max_retries = max_retries
        self.timeout = timeout
        # A session reuses one connection for every request and lets us
        # set the auth header once instead of on every call.
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Token {token}"

    def fetch_frames(self, norad_id: int, start: str | None = None, max_pages: int = 5) -> list[RawFrame]:
        """Return up to 'max_pages' pages of frames for one satellite.

        'start' is an optional ISO timestamp so we only ask for new frames.
        """
        params = {"satellite": norad_id, "format": "json"}
        if start:
            params["start"] = start

        frames: list[RawFrame] = []
        url = TELEMETRY_URL
        pages = 0
        while url and pages < max_pages:
            # Only send params on the first request. The "next" URL that
            # SatNOGS gives us already has them baked in.
            response = self._get(url, params if pages == 0 else None)
            data = response.json()
            entries = data["results"] if isinstance(data, dict) else data
            frames.extend(self._parse_entries(entries))
            url = self._next_page_url(response, data)
            pages += 1

        logger.info("NORAD %s: fetched %d frames over %d page(s)", norad_id, len(frames), pages)
        return frames

    def _get(self, url: str, params: dict | None) -> requests.Response:
        """GET with retries on rate limits (429) and server errors (5xx)."""
        for attempt in range(self.max_retries + 1):
            response = self.session.get(url, params=params, timeout=self.timeout)

            if response.status_code in (401, 403):
                raise SatNOGSAuthError(
                    "SatNOGS rejected the token. Use your DB token, not the Network token.")

            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.max_retries:
                response.raise_for_status()
                return response

            wait = self._backoff_seconds(response, attempt)
            logger.warning("HTTP %s from SatNOGS, retrying in %.0fs (attempt %d/%d)",
                           response.status_code, wait, attempt + 1, self.max_retries)
            time.sleep(wait)

        raise RuntimeError("unreachable")  # loop always returns or raises

    @staticmethod
    def _backoff_seconds(response: requests.Response, attempt: int) -> float:
        """Use the server's Retry-After if it gave one, else exponential backoff."""
        header = response.headers.get("Retry-After")
        try:
            wait = float(header) if header is not None else DEFAULT_BACKOFF_SECONDS * 2 ** attempt
        except ValueError:
            wait = DEFAULT_BACKOFF_SECONDS
        return min(max(wait, 0.0), MAX_BACKOFF_SECONDS)

    @staticmethod
    def _parse_entries(entries: list[dict]) -> list[RawFrame]:
        """Turn API entries into RawFrames, skipping any that are malformed."""
        frames = []
        for entry in entries:
            try:
                frames.append(RawFrame.from_api(entry))
            except (KeyError, ValueError, TypeError) as err:
                logger.warning("Skipping malformed entry (%s): %r", err, entry)
        return frames

    @staticmethod
    def _next_page_url(response: requests.Response, data) -> str | None:
        """SatNOGS may put the next page in the JSON body or the Link header."""
        if isinstance(data, dict) and data.get("next"):
            return data["next"]
        return response.links.get("next", {}).get("url")


def deduplicate(frames: list[RawFrame]) -> list[RawFrame]:
    """Drop frames heard by more than one ground station, keeping the first."""
    seen: set[str] = set()
    unique = []
    for frame in frames:
        if frame.dedup_key not in seen:
            seen.add(frame.dedup_key)
            unique.append(frame)
    return unique
