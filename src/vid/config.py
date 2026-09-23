"""Shared provider configuration: named profiles read from one TOML file.

Namespaced `[providers.*]`, not `[speech.*]` -- `vid.speech.openai_tts` is the
first consumer, but a model-backed `intelligence` implementation could reuse
the same profiles later. Nothing for `intelligence` is built here yet.

    [providers.openai.default]
    api_key_env = "OPENAI_API_KEY"

    [providers.openai.work]
    api_key_env = "WORK_OPENAI_KEY"
    base_url    = "https://gateway.example.com/v1"   # optional
    model       = "gpt-4o-mini-tts"                  # optional

`api_key_env` NAMES the environment variable to read; it is required per
profile, and a raw `api_key` value anywhere under `[providers.*]` is refused
outright -- this file is meant to be safe to share or commit.
"""

from __future__ import annotations

import os
from pathlib import Path
import tomllib

from vid.schemas import VidError


def config_path() -> Path:
    """Where `[providers.*]` profiles are read from.

    `VID_CONFIG` names the file directly (tests rely on this to avoid
    touching a real home directory). Otherwise `$XDG_CONFIG_HOME/vid/config.toml`,
    falling back to `~/.config`.
    """
    override = os.environ.get("VID_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "vid" / "config.toml"


def _raw_api_key_paths(node: object, path: str) -> list[str]:
    """Every dotted path to a literal `api_key` key found anywhere below `node`."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key == "api_key":
                found.append(here)
            found += _raw_api_key_paths(value, here)
    return found


def _load_raw(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise VidError(
            f"Could not parse {path}: {error}\n"
            "Fix the TOML syntax at that location, or remove the file to fall back to "
            "implicit defaults."
        ) from error

    offenders = _raw_api_key_paths(raw.get("providers", {}), "providers")
    if offenders:
        raise VidError(
            f"{path} stores a raw key at '{offenders[0]}'. Use 'api_key_env' to name the "
            "environment variable to read instead of the key itself -- this file is meant "
            "to be safe to share or commit."
        )
    return raw


def provider_profile(provider: str, profile: str, default_env_var: str | None = None) -> dict:
    """The named profile's config table for `provider`, e.g. `("openai", "work")`.

    Zero-config path: when NO config file exists at all and `profile` is
    `"default"`, an implicit profile reading `default_env_var` is synthesised
    -- no file to write for the common case. Anything else with no file is an
    error naming the path it looked for. A profile missing from an EXISTING
    file is an error listing the profiles that are defined for `provider`.
    """
    path = config_path()

    if not path.is_file():
        if profile == "default" and default_env_var is not None:
            return {"api_key_env": default_env_var}
        raise VidError(
            f"No config file at {path}, so profile {profile!r} is not defined. Add a "
            f"[providers.{provider}.{profile}] section there, or use the implicit "
            "'default' profile instead."
        )

    raw = _load_raw(path)
    provider_block = raw.get("providers", {}).get(provider, {})
    if profile not in provider_block:
        defined = sorted(provider_block) or ["(none)"]
        raise VidError(
            f"{path} does not define [providers.{provider}.{profile}]. Defined profiles for "
            f"{provider!r}: {', '.join(defined)}. Use one of those, or add a "
            f"[providers.{provider}.{profile}] section to define this one."
        )

    section = provider_block[profile]
    if not isinstance(section, dict) or "api_key_env" not in section:
        raise VidError(
            f"[providers.{provider}.{profile}] in {path} is missing 'api_key_env' -- it names "
            "the environment variable to read, and is required on every profile."
        )
    return section
