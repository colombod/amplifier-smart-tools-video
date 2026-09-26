"""A provider whose startup fails must say so, not print a traceback.

Deviation `failure-names-remedy`, `check-spec-adherence` run 7 at
`copilot.py:32-35`: the SDK client is constructed OUTSIDE `_run`, so a
constructor or SDK-initialisation failure had no conversion to `VidError` at
all. `_run` catches timeouts and SDK errors, but only once it is already
running, and `cli.main` translates only `VidError` into a named, non-zero exit.
Everything else reaches the user as a stack trace naming no remedy.

Same shape as the unguarded filesystem calls swept out of `index.py` and
`lib.py`, one layer over: a call that can fail, on a path whose failures are
supposed to be translated, with nothing translating it.

THE SECOND TEST IS THE LOAD-BEARING ONE. `self._github_token()` is evaluated as
an argument INSIDE the new `try`, and it already raises `VidError` naming the
exact remedy -- gh not installed, or gh not signed in. A blanket
`except Exception` would have swallowed those precise diagnoses and replaced
them with the generic startup message, which is a worse failure than the
traceback the guard removes. That regression was written, caught by reasoning
about the argument evaluation order, and is now pinned here.
"""

from __future__ import annotations

import pytest

from vid.intelligence.schemas import AgentRequest
from vid.schemas import VidError

copilot = pytest.importorskip("vid.intelligence.copilot", reason="needs the copilot SDK installed")


def _request() -> AgentRequest:
    return AgentRequest(prompt="anything", model="gpt-6-astra", timeout_seconds=30)


def _intelligence(monkeypatch, token="ghu_stand-in"):
    agent = copilot.CopilotIntelligence()
    monkeypatch.setattr(agent, "_github_token", lambda: token)
    return agent


def test_a_failing_sdk_constructor_becomes_a_named_error(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("SDK runtime could not be initialised")

    monkeypatch.setattr(copilot, "CopilotClient", explode)
    agent = _intelligence(monkeypatch)

    with pytest.raises(VidError) as failure:
        agent.run(_request())

    message = str(failure.value)
    assert "could not be started" in message, f"the failure does not say the provider failed to start: {message!r}"
    assert "SDK runtime could not be initialised" in message, f"the underlying cause is lost: {message!r}"
    assert "gh auth login" in message, f"the failure names no remedy: {message!r}"
    assert "vid check" in message, f"the failure does not point at `vid check`: {message!r}"


def test_a_precise_auth_error_is_not_replaced_by_the_generic_one(monkeypatch) -> None:
    """A VidError from token minting must pass through UNTOUCHED.

    This is the regression the guard itself introduced and then fixed: the
    token call happens inside the `try`, so a blanket handler would rewrap
    "the GitHub CLI is not signed in" as "the provider could not be started"
    and throw away the remedy the caller actually needed.
    """
    precise = "The GitHub CLI is not signed in: run `gh auth login` with a Copilot subscription."

    def refuse():
        raise VidError(precise)

    monkeypatch.setattr(copilot, "CopilotClient", lambda *a, **k: None)
    agent = copilot.CopilotIntelligence()
    monkeypatch.setattr(agent, "_github_token", refuse)

    with pytest.raises(VidError) as failure:
        agent.run(_request())

    assert str(failure.value) == precise, (
        f"a precise provider diagnosis was replaced by the generic startup message: {str(failure.value)!r}"
    )


# ---------------------------------------------------------------------------
# The OpenAI sibling of the copilot guard above, reported one run later. Both
# build a provider client outside any conversion; fixing the reported one and
# not its twin is the pattern this whole branch is about.
# ---------------------------------------------------------------------------

openai_tts = pytest.importorskip("vid.speech.openai_tts", reason="needs the openai extra installed")


def _backend(monkeypatch, base_url=None):
    backend = openai_tts.OpenAIBackend.__new__(openai_tts.OpenAIBackend)
    backend._api_key = "stand-in"
    backend._base_url = base_url
    backend._profile = "work"
    backend._model = "tts-1"
    backend.voice = "nova"
    return backend


def test_a_missing_openai_package_becomes_a_named_error() -> None:
    """The SDK import moved inside the guard, so an absent package is named too.

    This is the state of this machine and of CI: the `openai` extra is not
    installed, so `import openai` inside `_client` raises ModuleNotFoundError
    -- which `cli.main` does not translate. It now comes back as a VidError
    naming the profile and the remedy, and this test runs everywhere rather
    than skipping exactly where the failure is real.
    """
    pytest.importorskip  # noqa: B018 - referenced so the intent is explicit
    import importlib.util

    if importlib.util.find_spec("openai") is not None:
        pytest.skip("the openai extra IS installed here; the monkeypatched tests below cover it")

    with pytest.raises(VidError) as failure:
        _backend(None)._client()

    message = str(failure.value)
    assert "could not be created" in message, f"a missing SDK is not named: {message!r}"
    assert "'work'" in message, f"the failure does not name the profile: {message!r}"
    assert "vid check" in message, f"the failure names no remedy: {message!r}"


def test_a_failing_openai_client_becomes_a_named_error(monkeypatch) -> None:
    openai = pytest.importorskip("openai", reason="needs the openai extra installed")

    def explode(*args, **kwargs):
        raise RuntimeError("invalid base_url in profile")

    monkeypatch.setattr(openai, "OpenAI", explode)

    with pytest.raises(VidError) as failure:
        _backend(monkeypatch)._client()

    message = str(failure.value)
    assert "could not be created" in message, f"the failure does not say the client failed: {message!r}"
    assert "'work'" in message, f"the failure does not name the profile: {message!r}"
    assert "invalid base_url in profile" in message, f"the underlying cause is lost: {message!r}"
    assert "vid check" in message, f"the failure names no remedy: {message!r}"


def test_a_precise_vid_error_from_the_client_is_not_rewrapped(monkeypatch) -> None:
    """Same pass-through rule as the copilot guard."""
    openai = pytest.importorskip("openai", reason="needs the openai extra installed")

    precise = "That profile's api_key_env names a variable that is not set."

    def refuse(*args, **kwargs):
        raise VidError(precise)

    monkeypatch.setattr(openai, "OpenAI", refuse)

    with pytest.raises(VidError) as failure:
        _backend(monkeypatch)._client()

    assert str(failure.value) == precise
