# Library Reference

Every capability of Vid is reachable from `vid.lib`.
All other surfaces, including the CLI, are thin wrappers over the library and add no capability of their own.

## Intelligence

Model-backed capabilities run through the `Intelligence` protocol in `vid.intelligence.interface`:

```python
class Intelligence(Protocol):
    implementation: str

    def preflight(self) -> None: ...
    def run(self, request: AgentRequest) -> AgentResult: ...
```

`preflight` raises `VidError` naming what to configure when the implementation cannot run.
`run` executes one agent: `AgentRequest` holds the prompt, model, optional workspace, and optional output schema; `AgentResult` holds the text, structured output, or error.
Setting `AgentRequest.resume` to an earlier `AgentResult.session_id` continues that session instead of starting a fresh one, so the agent keeps what it learned.

`default_intelligence()` returns the shipped implementation, `CopilotIntelligence`, built on the [GitHub Copilot SDK](https://github.com/github/copilot-sdk) and signed in through the GitHub CLI.
Another implementation is a module satisfying the protocol and a branch in that factory.

## Manifest

The tool's `SMART_TOOL.md` as structured data: the frontmatter as fields, the Markdown below it as `Manifest.body`.

```python
def load_manifest() -> Manifest
```

## Skill

What an agent reads once it has decided to drive the tool: the manifest body and the capability list, wrapped so the reader knows where the tool's files are.
The CLI's `--help` prints exactly this.

```python
def skill() -> str
```

The installed package root, resolved at runtime, where the files the skill names can be read.

```python
def skill_directory() -> Path
```

The files the skill lists under `<skill_resources>`, as paths relative to `skill_directory()`. Every one ships inside the package, so each resolves after installation.

```python
def skill_resources() -> list[str]
```

The tool's canonical source, read from the package metadata's `[project.urls]` `Repository` entry, or `None` when the package declares none.
The skill carries it so a caller that can run the tool but not read its files still reaches the documentation.

```python
def repository_url() -> str | None
```

## Adding a capability

A capability's code goes in `vid/capabilities/<name>/`, with its prompts and templates beside it, and `lib.py` gets a facade function that imports it and is the only caller of it.
Each capability of the library gets a section here: what it does and when to reach for it, the signature `lib.py` exposes, what each argument means, and what it returns or raises.
Model-backed capabilities say so, and take `model` and `reasoning_effort`, defaulting to `DEFAULT_INTELLIGENCE_MODEL` and `low` from `vid.schemas`.

## Production audio and verification

```python
audio_replace(plan, track, start=0.0) -> Plan
audio_mix(plan, track, level=-18.0, start=0.0) -> Plan
render(plan, output, print_command=False, video_codec="libx264") -> str
verify(video, expect_no_black_frames=True, longest_black=0.5,
       pixel_threshold=0.1, frame_threshold=0.98) -> tuple[bool, str]
```

`start` is finite, nonnegative seconds into the edit at that operation, not a
seek into the supplied track. Replacement is silent before it; mixing retains
the original audio. Supplied audio is padded/truncated to the current duration.
Later edits move it with the picture. A dissolve blends the outgoing tail AND
incoming head: reserve silent handles of the transition duration on both, or
add narration after stitching on the final timeline.

`video_codec="copy"` is opt-in and rejects picture/timing operations. It requires
exactly one zero-start picture stream with known duration and matching MP4 or MOV
extensions. It preserves picture packets, not the entire file; audio uses AAC.
Other containers and conversions are not qualified. Defaults still re-encode.

Black detection thresholds are fractions in `0..1`: pixel luma (`pix_th`) and
frame coverage (`pic_th`). The report includes their values. Failed ffmpeg
analysis is an error, not evidence of no black frames.

## Model-backed public operations

`stitch`, `resolve_transition`, `find`, `index` (vision only), and `narrate`
accept `model=DEFAULT_INTELLIGENCE_MODEL` (currently `gpt-6-astra`) and
`reasoning_effort="low"`. Overrides reach every request, including generated
transitions and narration shortening retries. Named presets and literal
matches remain deterministic. Vision matching uses the descriptions already
stored in the index; changing the model does not regenerate cached descriptions.

The CLI exposes `--intelligence-model` and `--reasoning-effort` consistently,
with `--model` as an alias except on `index`, where it remains Whisper size
(`model_size` in the library).

Narration's script model is separate from its speech model. Piper remains local
and default. Explicit OpenAI speech defaults to `gpt-4o-mini-tts`; an explicit
profile `model` overrides it. No paid fallback is introduced.
