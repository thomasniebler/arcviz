"""Entra ID access tokens for Azure OpenAI (keyless, e.g. the container app's managed identity).

Spec: docs/architecture/specs/operations.md §5 (ADR-0115). `BearerTokenProvider` works over any credential with
azure-identity's `get_token(scope)` (tested with a fake); `default_credential()` imports azure-identity lazily (the
`azure-openai` extra). The OpenAI SDK calls the provider before every request, so it must be cheap: the token is
cached and refreshed `refresh_margin` seconds before it expires.
"""

from __future__ import annotations

import functools
import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Any

from ..envconfig import ConfigError

DEFAULT_SCOPE = "https://cognitiveservices.azure.com/.default"
DEFAULT_REFRESH_MARGIN = 300.0  # seconds before expiry; Entra tokens live 60-90 minutes
REFRESH_BACKOFF = 30.0  # seconds without a new attempt after a failure (the old token, if valid, is used meanwhile)
REFRESH_TIMEOUT = 30.0  # seconds a refresh may take; then it is abandoned and counts as a failure
MISSING_EXTRA = 'ARCVIZ_OPENAI_AUTH=entra needs azure-identity: pip install "arcviz-agent[azure-openai]".'

logger = logging.getLogger(__name__)


class TokenRefreshError(RuntimeError):
    """A refresh failed; raised to the callers that did not make the attempt, caused by (`from`) its exception."""


class BearerTokenProvider:
    """A callable returning a valid access token for `scope`; thread-safe, a single refresh in flight with a deadline,
    backed off after a failure."""

    def __init__(
        self,
        credential: Any,
        scope: str = DEFAULT_SCOPE,
        *,
        refresh_margin: float = DEFAULT_REFRESH_MARGIN,
        backoff: float = REFRESH_BACKOFF,
        refresh_timeout: float = REFRESH_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ):
        self.credential = credential
        self.scope = scope
        self.refresh_margin = refresh_margin
        self.backoff = backoff
        self.refresh_timeout = refresh_timeout
        self.clock = clock
        self._token: str | None = None
        self._expires_on = float("-inf")
        self._retry_after = float("-inf")  # no attempt before this after a failed one
        self._error: BaseException | None = None  # the last failure, the cause of the errors within the backoff
        self._round = 0  # number of the refresh in flight (or the last one)
        self._deadline = 0.0  # time.monotonic() at which the refresh in flight is abandoned
        self._refreshing = False  # single flight: one caller fetches, the others use the valid token or wait
        self._changed = threading.Condition()

    def __repr__(self) -> str:  # never the token
        return f"BearerTokenProvider(scope={self.scope!r})"

    def _valid(self, now: float) -> bool:
        return self._token is not None and self._expires_on > now

    def _settled(self, round_: int) -> bool:
        """The refresh `round_` ended (or a newer one began); called under the lock."""
        return not self._refreshing or self._round != round_

    def _shared_failure(self) -> TokenRefreshError:
        """A new exception for a caller that did not make the failed attempt (never the shared object itself)."""
        error = TokenRefreshError(f"The Entra token refresh failed ({type(self._error).__name__}).")
        error.__cause__ = self._error
        return error

    def __call__(self) -> str:
        with self._changed:
            while True:
                now = self.clock()
                if self._valid(now) and (
                    self._expires_on - self.refresh_margin > now or self._refreshing or now < self._retry_after
                ):
                    return self._token  # type: ignore[return-value]
                if not self._valid(now) and now < self._retry_after and self._error is not None:
                    raise self._shared_failure()  # within the backoff: fail fast, the credential is not asked again
                if not self._refreshing:
                    self._refreshing = True
                    self._round += 1
                    self._deadline = time.monotonic() + self.refresh_timeout
                    deadline = self._deadline
                    break
                waited_for = self._round  # no valid token: wait for the refresh in flight, at most to its deadline
                if not self._changed.wait_for(
                    functools.partial(self._settled, waited_for),
                    timeout=max(0.0, self._deadline - time.monotonic()),
                ):
                    raise TimeoutError(f"The Entra token refresh did not finish within {self.refresh_timeout:g} s.")
                if self._round == waited_for and self._error is not None and not self._valid(self.clock()):
                    raise self._shared_failure()
        try:
            # outside the lock: holders of a valid token never wait for the credential
            token, expires_on = self._fetch(deadline)
        except Exception as exc:
            if self._finish(error=exc):
                logger.warning(  # once per backoff window: no attempt is made within it
                    "The Entra token refresh failed (%s); using the current token for another %d s, next attempt "
                    "in %d s",
                    type(exc).__name__,
                    int(self._expires_on - self.clock()),
                    int(self.backoff),
                )
                return self._token  # type: ignore[return-value]
            raise
        except BaseException:
            self._finish()  # interrupted: nothing recorded, the next caller refreshes again
            raise
        self._finish(token=token, expires_on=expires_on)
        return token

    def _fetch(self, deadline: float) -> tuple[str, float]:
        """`credential.get_token` in a worker thread, abandoned at `deadline` (TimeoutError): a credential that hangs
        (an unreachable identity endpoint) never blocks the callers. The abandoned call ends on its own; its result
        is dropped."""
        outcome: list[Any] = []
        done = threading.Event()

        def run() -> None:
            try:
                outcome.append(_checked(self.credential.get_token(self.scope)))
            except BaseException as exc:  # handed to the caller below
                outcome.append(exc)
            finally:
                done.set()

        threading.Thread(target=run, name="arcviz-entra-refresh", daemon=True).start()
        if not done.wait(max(0.0, deadline - time.monotonic())):
            raise TimeoutError(f"The Entra token refresh did not finish within {self.refresh_timeout:g} s.")
        (result,) = outcome
        if isinstance(result, BaseException):
            raise result
        return result

    def _finish(self, token: str | None = None, expires_on: float = 0.0, error: Exception | None = None) -> bool:
        """End the refresh in flight (always, so no caller waits for it); whether a valid token remains."""
        with self._changed:
            self._refreshing = False
            if token is not None:
                self._token, self._expires_on = token, expires_on
                self._retry_after, self._error = float("-inf"), None
            elif error is not None:
                self._retry_after, self._error = self.clock() + self.backoff, error
            self._changed.notify_all()
            return self._valid(self.clock())


def _checked(access: Any) -> tuple[str, float]:
    """The token and expiry of an AccessToken; ValueError when the credential returned something else."""
    token = getattr(access, "token", None)
    expires_on = getattr(access, "expires_on", None)
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(expires_on, int | float)
        or not math.isfinite(expires_on)
    ):
        raise ValueError("The credential returned an invalid access token (no token or expiry).")
    return token, float(expires_on)


def default_credential() -> Any:
    """azure-identity's `DefaultAzureCredential` (managed identity, workload identity, Azure CLI, …)."""
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise ConfigError([MISSING_EXTRA]) from exc
    return DefaultAzureCredential()
