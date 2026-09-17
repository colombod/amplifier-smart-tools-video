"""What an intelligence implementation is asked to do, and what it answers with."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vid.schemas import ReasoningEffort


class HostWorkspace(BaseModel):
    """A directory on this machine; the agent's tools execute here."""

    path: Path


class AgentRequest(BaseModel):
    """One agent run: a prompt, a model, and optionally a place to work and a shape to answer in."""

    prompt: str
    model: str
    workspace: HostWorkspace | None = Field(
        default=None, description="Where the agent may read and run things; None means a plain completion, no tools"
    )
    writable: bool = Field(default=False, description="Whether the agent may create and modify files in the workspace")
    output_schema: dict[str, Any] | None = Field(
        default=None, description="JSON schema the structured output must satisfy"
    )
    reasoning_effort: ReasoningEffort = "low"
    timeout_seconds: int = Field(gt=0)
    resume: str | None = Field(
        default=None,
        description="The session_id of an earlier result to continue in, so the agent keeps what it learned; None starts fresh",
    )


class AgentResult(BaseModel):
    """The run's outcome; in-flight agent failures set `error` instead of raising."""

    output: dict[str, Any] | None = Field(
        default=None, description="The structured output, conforming to the request's schema when one was given"
    )
    text: str = Field(default="", description="The agent's final message")
    error: str | None = None
    session_id: str | None = Field(
        default=None, description="Identifies this run's session so a later request can resume it"
    )
