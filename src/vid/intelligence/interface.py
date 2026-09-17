"""The contract every model-backed capability runs through, so the implementation is swappable."""

from typing import Protocol

from vid.intelligence.schemas import AgentRequest, AgentResult


class Intelligence(Protocol):
    """Runs agents against models; the library depends on this contract, never on an SDK."""

    implementation: str

    def preflight(self) -> None:
        """Raise VidError naming exactly what to configure when the implementation cannot run."""
        ...

    def run(self, request: AgentRequest) -> AgentResult:
        """Run one agent to completion.

        When the request carries an output schema, the result either holds a conforming
        `output` or an `error`; retry mechanics are the implementation's own business.
        """
        ...


def default_intelligence() -> Intelligence:
    from vid.intelligence.copilot import CopilotIntelligence

    return CopilotIntelligence()
