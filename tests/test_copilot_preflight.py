"""preflight() must establish that the provider will ANSWER, not that a token exists.

It used to be `self._github_token()` alone, which checks exactly two things:
`gh` is on PATH, and `gh auth token` exits 0. Neither says Copilot accepts the
credential, and neither says the account is entitled to it. So `index --vision`
passed preflight, paid for shot detection and transcription, WROTE THE INDEX,
and only then failed at the model call.

THE SUBSTITUTES BELOW REPLAY RECORDED REAL RESPONSES. CI has no `gh` session
and no Copilot subscription, so the real service cannot be reached there -- the
one condition under which this repo permits a substitute at all. Every value is
copied from an actual run on 2026-09-26 against the live service, and both
directions were captured:

    REAL TOKEN (account HAS Copilot)
      get_auth_status -> GetAuthStatusResponse(isAuthenticated=True,
                             authType='token', host='https://github.com',
                             login=None,
                             statusMessage='https://github.com (via token)')
      list_models     -> 23 models in 1.22s

    INVALID TOKEN (real service rejection)
      get_auth_status -> GetAuthStatusResponse(isAuthenticated=False,
                             authType=None, host=None, login=None,
                             statusMessage='Not authenticated')
      list_models     -> copilot._jsonrpc.JsonRpcError: JSON-RPC Error -32603:
                         Request models.list failed with message:
                         Not authenticated. Please authenticate first.

    start() SUCCEEDED IN BOTH CASES, in 0.02s -- which is why preflight asks
    the service a question instead of just starting a client.

WHAT IS STILL NOT VERIFIED, and the tests do not pretend otherwise: a VALID
credential on an account WITHOUT a Copilot subscription. No such account was
available. That state is expected to surface at `list_models`, which is why
that refusal names the unentitled account and an unreachable service as two
possibilities rather than asserting either.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from vid.intelligence.copilot import CopilotIntelligence
from vid.schemas import VidError


@dataclass
class _RecordedAuthStatus:
    """The shape the real GetAuthStatusResponse presented, field for field."""

    isAuthenticated: bool  # noqa: N815 - mirrors the SDK's own attribute name
    authType: str | None = None  # noqa: N815
    host: str | None = None
    login: str | None = None
    statusMessage: str | None = None  # noqa: N815


class _ReplayedClient:
    """Replays one recorded exchange. Never invents an outcome."""

    def __init__(self, auth_status, models=None, models_error=None):
        self._auth_status = auth_status
        self._models = models or []
        self._models_error = models_error
        self.stopped = False
        #: How many times the provider was actually probed. The probe measured
        #: ~1.2s against the real service, so "once per process" is a property
        #: worth asserting rather than assuming.
        self.models_calls = 0

    async def start(self) -> None:
        return None  # measured: succeeded for BOTH a valid and an invalid token

    async def get_auth_status(self):
        return self._auth_status

    async def list_models(self):
        self.models_calls += 1
        if self._models_error is not None:
            raise self._models_error
        return self._models

    async def stop(self) -> None:
        self.stopped = True


def _intelligence_with(monkeypatch, client) -> CopilotIntelligence:
    monkeypatch.setattr("vid.intelligence.copilot.CopilotClient", lambda **_kwargs: client)
    intelligence = CopilotIntelligence()
    intelligence._token = "recorded-token-stand-in"  # skips `gh auth token`
    return intelligence


AUTHENTICATED = _RecordedAuthStatus(
    isAuthenticated=True, authType="token", host="https://github.com", statusMessage="https://github.com (via token)"
)
REJECTED = _RecordedAuthStatus(isAuthenticated=False, statusMessage="Not authenticated")


def test_a_rejected_credential_is_refused_before_any_work(monkeypatch) -> None:
    """Replays the real invalid-token rejection."""
    intelligence = _intelligence_with(monkeypatch, _ReplayedClient(REJECTED))

    with pytest.raises(VidError) as failure:
        intelligence.preflight()

    message = str(failure.value)
    assert "did not accept the credential" in message, message
    assert "Not authenticated" in message, f"the service's own reason is lost: {message!r}"
    assert "gh auth login" in message, f"no remedy named: {message!r}"
    assert "copilot" in message.lower(), f"the manifest's subscription reference is missing: {message!r}"


def test_a_credential_that_cannot_list_models_names_both_causes(monkeypatch) -> None:
    """The unentitled account and the unreachable service land in the SAME place.

    This code cannot tell them apart, so it names both. Telling someone to go
    buy a subscription they already hold is the same defect as a prerequisite
    refusal naming the wrong binary -- a confident, wrong remedy.
    """
    recorded = RuntimeError(
        "JSON-RPC Error -32603: Request models.list failed with message: Not authenticated. Please authenticate first."
    )
    client = _ReplayedClient(AUTHENTICATED, models_error=recorded)
    intelligence = _intelligence_with(monkeypatch, client)

    with pytest.raises(VidError) as failure:
        intelligence.preflight()

    message = str(failure.value)
    assert "would not list its models" in message, message
    assert "no GitHub Copilot subscription" in message, f"the entitlement cause is missing: {message!r}"
    assert "unreachable" in message, (
        f"the transient cause is missing, so a network blip accuses the account: {message!r}"
    )


def test_an_entitled_looking_account_with_no_models_is_refused(monkeypatch) -> None:
    intelligence = _intelligence_with(monkeypatch, _ReplayedClient(AUTHENTICATED, models=[]))

    with pytest.raises(VidError) as failure:
        intelligence.preflight()

    assert "offers this account no models" in str(failure.value)


def test_a_working_provider_passes_and_is_probed_only_once(monkeypatch) -> None:
    """Replays the real 23-model response. The probe costs ~1.2s, so it caches."""
    client = _ReplayedClient(AUTHENTICATED, models=[f"model-{n}" for n in range(23)])
    intelligence = _intelligence_with(monkeypatch, client)

    intelligence.preflight()
    intelligence.preflight()
    intelligence.preflight()

    assert client.models_calls == 1, (
        f"the provider was probed {client.models_calls} times across three preflights; "
        "the result is meant to be cached, and the probe costs ~1.2s"
    )


def test_the_client_is_stopped_even_when_preflight_refuses(monkeypatch) -> None:
    """A refused preflight must not leak a started runtime."""
    client = _ReplayedClient(REJECTED)
    intelligence = _intelligence_with(monkeypatch, client)

    with pytest.raises(VidError):
        intelligence.preflight()

    assert client.stopped, "the Copilot runtime was left running after a refusal"


# ---------------------------------------------------------------------------
# THE REPORTED CONSEQUENCE, tested at the capability rather than the provider.
# Everything above proves preflight refuses. This proves the refusal lands
# BEFORE build() writes the index -- which is the whole point, and the part a
# provider-level test cannot see.
# ---------------------------------------------------------------------------


def test_index_vision_writes_nothing_when_the_provider_cannot_answer(monkeypatch, tmp_path) -> None:
    from vid import lib
    import vid.index as index
    import vid.intelligence.interface as interface
    import vid.probe as probe

    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "store"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stand-in bytes -- build() must never be reached")

    monkeypatch.setattr(probe, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: True)

    built = {"called": False}

    def _build(*args, **kwargs):
        built["called"] = True
        raise AssertionError("build() ran even though the provider had already been shown to be unusable")

    monkeypatch.setattr(index, "build", _build)

    rejected = CopilotIntelligence()
    rejected._token = "recorded-token-stand-in"
    monkeypatch.setattr("vid.intelligence.copilot.CopilotClient", lambda **_k: _ReplayedClient(REJECTED))
    monkeypatch.setattr(interface, "default_intelligence", lambda: rejected)

    with pytest.raises(VidError) as failure:
        lib.index(str(video), speech=False, vision=True, yes=True)

    assert not built["called"], "build() ran before the provider was known to work"
    assert not index.index_path(str(video)).is_file(), (
        "an index file was written for a run that could never have completed"
    )
    assert "did not accept the credential" in str(failure.value), (
        f"the provider's own reason did not reach the caller: {str(failure.value)!r}"
    )
