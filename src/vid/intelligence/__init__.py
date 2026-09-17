def ask(intelligence, prompt: str, *, timeout_seconds: int = 60) -> str:
    """Run one prompt and return the agent's text, or raise with the reason.

    THIS EXISTS BECAUSE THE CONTRACT WAS GUESSED AT TWICE. Both `find` and
    `transitions` built `AgentRequest(instruction=...)` from memory, and the real
    field is `prompt` -- with `model` and `timeout_seconds` also required. Neither
    call site would have survived its first real invocation, and neither failure
    would have shown up until a user configured a provider.

    Reading the result needed the same care: `output` is a dict (the structured
    form, when a schema was given) and `text` is the agent's message. Treating
    `output` as a string would have worked in a test double and broken against
    the real SDK.
    """
    from vid.intelligence.schemas import AgentRequest
    from vid.schemas import DEFAULT_INTELLIGENCE_MODEL, VidError

    result = intelligence.run(
        AgentRequest(
            prompt=prompt,
            model=DEFAULT_INTELLIGENCE_MODEL,
            timeout_seconds=timeout_seconds,
        )
    )
    if getattr(result, "error", None):
        raise VidError(f"The model could not answer: {result.error}")
    text = (getattr(result, "text", "") or "").strip()
    if not text:
        raise VidError("The model returned an empty answer.")
    return text
