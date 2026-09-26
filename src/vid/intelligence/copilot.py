"""GitHub Copilot SDK implementation of the intelligence interface."""

import asyncio
import contextlib
from importlib.metadata import version
import shutil
import subprocess
import time
from typing import Any

from copilot import CopilotClient, PermissionHandler, Tool, ToolInvocation, ToolResult
import jsonschema

from vid.core.manifest import manifest_install as _manifest_install
from vid.intelligence.schemas import AgentRequest, AgentResult
from vid.schemas import VidError

SUBMIT_TOOL = "submit"
MAX_INVALID_SUBMISSIONS = 2


class CopilotIntelligence:
    """Runs agents through a Copilot CLI runtime on this machine, signed in as the GitHub CLI's user."""

    def __init__(self) -> None:
        self.implementation = f"copilot-sdk {version('github-copilot-sdk')}"
        self._token: str | None = None
        self._provider_reachable = False

    def preflight(self) -> None:
        """Establish that the provider will actually ANSWER, not just that a token exists.

        This used to be `self._github_token()` alone, which checks exactly two
        things: `gh` is on PATH, and `gh auth token` exits 0. Neither says the
        credential is accepted by Copilot, and neither says this account is
        entitled to it. So `index --vision` passed preflight, paid for shot
        detection and transcription, WROTE THE INDEX, and only then failed at
        the model call -- a preflight reporting "pass" for the one thing that
        was going to fail.

        MEASURED against the real service, both directions:

            valid token      get_auth_status -> isAuthenticated=True
                             list_models     -> 23 models in 1.22s
            invalid token    get_auth_status -> isAuthenticated=False
                             list_models     -> JsonRpcError -32603
                                                "Not authenticated."

        `start()` SUCCEEDED IN BOTH CASES, in 0.02s, so starting the client is
        not a check at all -- which is why this asks the service something.

        Roughly 1.2s, once per process and cached, against shot detection and
        transcription measured in minutes. Paying it here is what stops the
        expensive work from being spent on a run that cannot finish.
        """
        token = self._github_token()
        if self._provider_reachable:
            return
        asyncio.run(self._verify_provider_answers(token))
        self._provider_reachable = True

    async def _verify_provider_answers(self, token: str) -> None:
        """Ask the provider two questions whose answers are structured, not prose."""
        client = CopilotClient(github_token=token)
        try:
            try:
                await client.start()
            except Exception as exc:
                raise VidError(
                    f"The GitHub Copilot provider could not be started: {exc}.\n"
                    "Check that the copilot SDK is installed -- `vid check` reports what this "
                    "installation can actually do."
                ) from exc

            # A STRUCTURED BOOLEAN, not a parsed message. Verified to be True
            # for a working credential and False for a rejected one.
            status = await client.get_auth_status()
            if not getattr(status, "isAuthenticated", False):
                detail = getattr(status, "statusMessage", None) or "not authenticated"
                raise VidError(
                    f"GitHub Copilot did not accept the credential from `gh auth token` ({detail}). "
                    "Run `gh auth login` with an account that has a GitHub Copilot subscription "
                    f"({_manifest_install('github-copilot-subscription')})."
                )

            try:
                models = await client.list_models()
            except Exception as exc:
                # NAMES BOTH CAUSES, because this code cannot tell them apart
                # and guessing would be the worse failure. An unentitled
                # account and an unreachable service both land here, and
                # telling someone to go buy a subscription they already have
                # is exactly the wrong remedy -- the same defect as the
                # prerequisite refusals that named the wrong binary.
                raise VidError(
                    f"GitHub Copilot accepted the credential but would not list its models ({exc}).\n"
                    "Either this account has no GitHub Copilot subscription "
                    f"({_manifest_install('github-copilot-subscription')}), or the service is "
                    "unreachable from here. `vid check` reports what this installation can do."
                ) from exc

            if not models:
                raise VidError(
                    "GitHub Copilot accepted the credential but offers this account no models, "
                    "so a model-backed capability has nothing to run on. Check the account has a "
                    f"GitHub Copilot subscription ({_manifest_install('github-copilot-subscription')})."
                )
        finally:
            with contextlib.suppress(Exception):
                await client.stop()

    def run(self, request: AgentRequest) -> AgentResult:
        working_directory = str(request.workspace.path) if request.workspace is not None else None
        # THE SDK CLIENT IS CONSTRUCTED OUTSIDE `_run`, so until this guard
        # existed a constructor or SDK-initialisation failure had no conversion
        # to VidError at all: `_run` catches timeouts and SDK errors, but only
        # once it is already running, and `cli.main` translates only VidError.
        # A provider whose startup failed therefore reached the user as a raw
        # traceback naming no remedy -- the same shape as the unguarded
        # filesystem calls swept out of index.py and lib.py, one layer over.
        try:
            client = CopilotClient(working_directory=working_directory, github_token=self._github_token())
        except VidError:
            # STRAIGHT THROUGH, NEVER REWRAPPED. `self._github_token()` is
            # evaluated as an argument inside this `try`, and it already
            # raises VidError naming the exact remedy -- gh is not installed,
            # or gh is not signed in. Swallowing those into the general
            # message below would replace a precise diagnosis with a vague
            # one, which is a worse failure than the traceback this guard
            # exists to remove.
            raise
        except Exception as exc:
            raise VidError(
                f"The GitHub Copilot provider could not be started: {exc}.\n"
                "Check that `gh auth login` has been run with an account carrying a GitHub "
                "Copilot subscription, and that the copilot SDK is installed -- `vid check` "
                "reports what this installation can actually do."
            ) from exc
        return asyncio.run(self._run(client, request))

    def _github_token(self) -> str:
        if self._token is not None:
            return self._token
        if shutil.which("gh") is None:
            raise VidError(
                "Model-backed capabilities need the GitHub CLI. "
                f"Install it ({_manifest_install('gh')}) and sign in with `gh auth login`."
            )
        minted = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if minted.returncode != 0:
            raise VidError(
                f"The GitHub CLI is not signed in: {minted.stderr.strip()} "
                "Run `gh auth login` with an account that has a GitHub Copilot subscription "
                f"({_manifest_install('github-copilot-subscription')})."
            )
        self._token = minted.stdout.strip()
        return self._token

    async def _run(self, client: CopilotClient, request: AgentRequest) -> AgentResult:
        submitted: dict[str, Any] | None = None

        def capture(invocation: ToolInvocation) -> ToolResult:
            nonlocal submitted
            submitted = invocation.arguments
            return ToolResult(text_result_for_llm="Submission received.")

        session_options: dict[str, Any] = {
            "model": request.model,
            "reasoning_effort": request.reasoning_effort,
            "on_permission_request": PermissionHandler.approve_all,
            "skip_custom_instructions": True,
            "available_tools": [],
        }
        if request.workspace is not None:
            session_options["working_directory"] = str(request.workspace.path)
            session_options["available_tools"] = ["view", "grep", "bash"]
            if request.writable:
                # bash is not sandboxed to the workspace; the caller's prompt bounds the agent
                # and the caller validates before anything the agent wrote is kept.
                session_options["available_tools"] = [*session_options["available_tools"], "edit", "write"]
        if request.output_schema is not None:
            session_options["tools"] = [
                Tool(
                    name=SUBMIT_TOOL,
                    description="Submit your final answer. Call it exactly once, when you are done.",
                    parameters=request.output_schema,
                    handler=capture,
                    skip_permission=True,
                    is_terminal=True,
                )
            ]
            session_options["available_tools"] = [*session_options["available_tools"], SUBMIT_TOOL]
        deadline = time.monotonic() + request.timeout_seconds
        # Carried out of the try so the failure paths can name the session the caller could resume.
        session_id: str | None = None
        try:
            await client.start()
            if request.resume is not None:
                session = await client.resume_session(request.resume, **session_options)
            else:
                session = await client.create_session(**session_options)
            session_id = session.session_id
            prompt = request.prompt
            invalid = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    event = await session.send_and_wait(prompt, timeout=remaining)
                except TimeoutError:
                    await session.abort()
                    return AgentResult(
                        error=f"The agent did not finish within {request.timeout_seconds} seconds.",
                        session_id=session_id,
                    )
                text = str(getattr(event.data, "content", "") or "") if event is not None else ""
                if request.output_schema is None:
                    return AgentResult(text=text, session_id=session_id)
                problem = _submission_problem(submitted, request.output_schema)
                if problem is None:
                    return AgentResult(output=submitted, text=text, session_id=session_id)
                if invalid >= MAX_INVALID_SUBMISSIONS:
                    return AgentResult(
                        text=text,
                        error=f"No valid submission after {invalid} retries: {problem}",
                        session_id=session_id,
                    )
                invalid += 1
                submitted = None
                prompt = (
                    f"Your answer was not accepted: {problem}. "
                    f"Call the {SUBMIT_TOOL} tool now with an answer matching its schema."
                )
        except TimeoutError:
            return AgentResult(
                error=f"The agent did not finish within {request.timeout_seconds} seconds.", session_id=session_id
            )
        except Exception as error:  # an SDK or runtime failure is the caller's data, not a crash
            return AgentResult(error=f"{type(error).__name__}: {error}", session_id=session_id)
        finally:
            with contextlib.suppress(Exception):
                await client.stop()


def _submission_problem(submitted: dict[str, Any] | None, schema: dict[str, Any]) -> str | None:
    if submitted is None:
        return f"the {SUBMIT_TOOL} tool was never called"
    try:
        jsonschema.validate(submitted, schema)
    except jsonschema.ValidationError as error:
        return f"the submission does not match the schema ({error.message})"
    return None
