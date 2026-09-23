"""Record a real OpenAI TTS response into a committable test fixture.

This is the one thing in this repo that must be run BY A HUMAN, ahead of
time, with a real key -- nothing in CI or in the ordinary test suite calls
this. `tests/test_speech.py`'s replay test skips cleanly when no fixture is
present and names this script as the remedy.

Usage:
    OPENAI_API_KEY=sk-... uv run scripts/record_openai_fixture.py
    uv run scripts/record_openai_fixture.py --voice alloy --speed 1.0 --env-var WORK_OPENAI_KEY

Writes:
    tests/fixtures/openai_tts/sample.wav   -- the raw response bytes
    tests/fixtures/openai_tts/sample.json  -- provenance: model, voice, speed,
                                               the input text, the openai SDK
                                               version, and when it was recorded
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from vid.speech.openai_tts import DEFAULT_OPENAI_MODEL

FIXTURE_DIR = Path(__file__).parents[1] / "tests" / "fixtures" / "openai_tts"
DEFAULT_TEXT = "This is a recorded fixture for vid's OpenAI speech backend."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="alloy")
    parser.add_argument("--model", default=DEFAULT_OPENAI_MODEL)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--env-var", default="OPENAI_API_KEY", help="Env var to read the API key from.")
    parser.add_argument("--out-dir", type=Path, default=FIXTURE_DIR)
    args = parser.parse_args()

    api_key = os.environ.get(args.env_var)
    if not api_key:
        raise SystemExit(f"{args.env_var} is not set. Export a real OpenAI API key first.")

    import openai

    client = openai.OpenAI(api_key=api_key)
    response = client.audio.speech.create(
        model=args.model,
        voice=args.voice,
        input=args.text,
        response_format="wav",
        speed=args.speed,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = args.out_dir / "sample.wav"
    provenance_path = args.out_dir / "sample.json"

    wav_path.write_bytes(response.content)
    provenance_path.write_text(
        json.dumps(
            {
                "model": args.model,
                "voice": args.voice,
                "speed": args.speed,
                "text": args.text,
                "openai_sdk_version": openai.__version__,
                "recorded_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {wav_path} and {provenance_path}")


if __name__ == "__main__":
    main()
