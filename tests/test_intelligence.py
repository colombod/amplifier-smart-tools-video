"""`ask`'s failure paths must name a remedy, not just relay the provider's raw
error with nothing a caller (usually an agent) can act on.
"""

from __future__ import annotations

import pytest

from vid.intelligence import ask
from vid.intelligence.schemas import AgentResult
from vid.schemas import VidError


class _FailingIntelligence:
    """A provider that always reports an in-flight failure, the same shape
    `CopilotIntelligence.run` uses -- `error` set, not an exception raised."""

    def __init__(self, error: str) -> None:
        self.error = error

    def run(self, request) -> AgentResult:
        return AgentResult(text="", error=self.error)


class _EmptyIntelligence:
    def run(self, request) -> AgentResult:
        return AgentResult(text="", error=None)


def test_ask_names_a_remedy_when_the_provider_errors():
    with pytest.raises(VidError) as failure:
        ask(_FailingIntelligence("the session timed out"), "a prompt")

    message = str(failure.value)
    assert "the session timed out" in message
    assert "gh auth login" in message
    assert "vid check" in message


def test_ask_refuses_an_empty_answer_and_names_a_remedy():
    with pytest.raises(VidError, match="empty answer") as failure:
        ask(_EmptyIntelligence(), "a prompt")

    message = str(failure.value)
    assert "retry" in message.lower(), f"no remedy telling the caller to retry:\n{message}"
    assert "vid check" in message, f"no remedy pointing at `vid check`:\n{message}"
