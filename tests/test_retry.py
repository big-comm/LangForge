"""Tests for transient API retry classification and timing."""

from http import HTTPStatus
from datetime import datetime, timezone

import pytest
import requests

from api import base


def _http_error(
    status: int,
    *,
    retry_after: str | None = None,
    message: str = "API request failed",
) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.HTTPError(message, response=response)


@pytest.fixture
def retry_clock(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(base.time, "sleep", sleeps.append)
    monkeypatch.setattr(base.random, "uniform", lambda _low, high: high)
    return sleeps


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 599])
def test_retries_transient_http_statuses(status, retry_clock):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(status)
        return "ok"

    assert request() == "ok"
    assert calls == 2
    assert retry_clock == [base._INITIAL_BACKOFF]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_does_not_retry_permanent_http_statuses(status, retry_clock):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        raise _http_error(
            status,
            retry_after="1",
            message="429 timeout RESOURCE_EXHAUSTED retryDelay: 1s",
        )

    with pytest.raises(requests.HTTPError):
        request()

    assert calls == 1
    assert retry_clock == []


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectTimeout("connect timeout"),
        requests.exceptions.ConnectionError("connection failed"),
        TimeoutError("socket timeout"),
        ConnectionError("socket disconnected"),
    ],
    ids=[
        "requests-timeout",
        "requests-connection",
        "builtin-timeout",
        "builtin-connection",
    ],
)
def test_retries_concrete_connection_and_timeout_errors(error, retry_clock):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return "ok"

    assert request() == "ok"
    assert calls == 2
    assert retry_clock == [base._INITIAL_BACKOFF]


def test_does_not_classify_generic_error_text_as_transient(retry_clock):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        raise RuntimeError("429 rate quota timeout RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError):
        request()

    assert calls == 1
    assert retry_clock == []


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("0", 0.0),
        ("2.5", 2.5),
        ("9999", base._MAX_RETRY_DELAY),
    ],
)
def test_respects_bounded_numeric_retry_after(
    retry_after,
    expected,
    retry_clock,
):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429, retry_after=retry_after)
        return "ok"

    assert request() == "ok"
    assert retry_clock == [expected]


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("Sat, 25 Jul 2026 12:00:30 GMT", 30.0),
        ("Sat, 25 Jul 2026 13:10:00 GMT", base._MAX_RETRY_DELAY),
        ("Sat, 25 Jul 2026 11:59:30 GMT", 0.0),
    ],
)
def test_respects_bounded_http_date_retry_after(
    retry_after,
    expected,
    retry_clock,
    monkeypatch,
):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(base.time, "time", lambda: now)
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429, retry_after=retry_after)
        return "ok"

    assert request() == "ok"
    assert retry_clock == [expected]


def test_invalid_retry_after_uses_jittered_backoff(retry_clock):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(
                503,
                retry_after="not-a-date",
            )
        return "ok"

    assert request() == "ok"
    assert retry_clock == [base._INITIAL_BACKOFF]


def test_uses_gemini_retry_delay_after_structured_status(retry_clock):
    class GeminiError(Exception):
        code = HTTPStatus.TOO_MANY_REQUESTS

    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise GeminiError("RESOURCE_EXHAUSTED {'retryDelay': '7.25s'}")
        return "ok"

    assert request() == "ok"
    assert retry_clock == [7.25]


def test_retry_after_takes_precedence_over_gemini_delay(retry_clock):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(
                429,
                retry_after="3",
                message="retryDelay: 9s",
            )
        return "ok"

    assert request() == "ok"
    assert retry_clock == [3.0]


def test_symbolic_sdk_status_is_classified_without_message_matching(
    retry_clock,
):
    class SDKError(Exception):
        status = "RESOURCE_EXHAUSTED"

    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SDKError("retry in 1.5s")
        return "ok"

    assert request() == "ok"
    assert retry_clock == [1.5]


def test_exponential_backoff_has_equal_jitter(monkeypatch):
    sleeps: list[float] = []
    jitter_ranges: list[tuple[float, float]] = []

    def jitter(low, high):
        jitter_ranges.append((low, high))
        return (low + high) / 2

    monkeypatch.setattr(base.time, "sleep", sleeps.append)
    monkeypatch.setattr(base.random, "uniform", jitter)
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise _http_error(503)
        return "ok"

    assert request() == "ok"
    assert jitter_ranges == [(1.0, 2.0), (2.0, 4.0)]
    assert sleeps == [1.5, 3.0]


def test_never_calls_more_than_max_retries(retry_clock):
    calls = 0

    @base.retry_on_rate_limit
    def request():
        nonlocal calls
        calls += 1
        raise _http_error(503)

    with pytest.raises(requests.HTTPError):
        request()

    assert calls == base._MAX_RETRIES
    assert retry_clock == [2.0, 4.0, 8.0, 16.0]
