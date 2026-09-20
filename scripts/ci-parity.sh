#!/usr/bin/env bash
# Run this repo's CI gates inside a container shaped like the runner
# .github/workflows/ci.yml actually uses: ubuntu-latest (Ubuntu 24.04) with
# ffmpeg installed via `apt-get install -y ffmpeg`, same as every CI job.
#
# WHY THIS EXISTS: a dev machine's ffmpeg is whatever the developer has --
# here, a bleeding-edge nightly build -- and a filter graph the nightly
# accepts can be flatly rejected by the distro's stable ffmpeg (commit
# bfeaa13: exit 234 on 6.1.1, invisible on this box, immediate on CI). "Green
# on my machine" only means "green on CI" when the two machines agree on the
# one dependency that actually varies. This script makes that agreement
# checkable before a push, not after a red run.
#
# USAGE
#   scripts/ci-parity.sh              # everything: prek, type-check, test-job replica
#   scripts/ci-parity.sh test         # just the `test` CI job replica (ruff + pytest)
#   scripts/ci-parity.sh typecheck    # just the `type-check` CI job replica (ty)
#   scripts/ci-parity.sh prek         # just `prek run --all-files`
#   scripts/ci-parity.sh shell        # interactive shell in the image, repo mounted
#   scripts/ci-parity.sh clean        # remove the image and all cached volumes
#
# ENV OVERRIDES (for reproducing a historical commit in a second checkout,
# e.g. a `git worktree`, without disturbing this checkout's own caches):
#   VID_REPO_DIR    -- path to the checkout to run against (default: this repo)
#   VID_VOL_SUFFIX  -- suffix for the cache volume names (default: "main"),
#                      so a worktree gets its own venvs/uv-cache rather than
#                      sharing (and corrupting) this checkout's.
#
# WHAT IT CREATES
#   - one Docker image, `vid-ci-parity`, built from docker/ci-parity.Dockerfile.
#     Rebuilds only when that Dockerfile changes (normal layer caching) --
#     `docker build` is a no-op cache hit otherwise, so repeat runs do not
#     reinstall ubuntu/ffmpeg/uv/prek.
#   - three named volumes per VID_VOL_SUFFIX, so dependency installs are not
#     repeated per run:
#       vid-ci-parity-uv-cache-<suffix>   -- uv's package cache
#       vid-ci-parity-venv-test-<suffix>  -- the `test` job's .venv (mounted
#                                             AT <repo>/.venv)
#       vid-ci-parity-venv-dev-<suffix>   -- the `type-check`/prek job's
#                                             .venv-equivalent
#     These are named, not anonymous, and are mounted OVER a path inside the
#     bind-mounted repo rather than sharing the host's own .venv -- a venv
#     holds compiled/native wheels (ruff, ty) that are not safe to share
#     between the host's libc and the container's. The `test` volume is
#     mounted at the literal path <repo>/.venv (not a differently-named one):
#     tests/test_no_provider_no_ffmpeg.py hardcodes `REPO / ".venv" / "bin" /
#     "vid"` to find the installed CLI, the same way CI's own `test` job
#     names it -- so this has to be ".venv", in the container's view only.
#     The host's real .venv on disk is never touched; the named volume simply
#     shadows that one path for the lifetime of the container.
#   - no container is ever left running: every invocation uses `docker run --rm`.
#
#   The workspace root ABOVE the repo (not just the repo itself) is bind-mounted,
#   at the SAME absolute path inside the container as on the host. This repo is
#   normally checked out as a git submodule (`.git` is a file with a RELATIVE
#   `gitdir: ../.git/modules/...` pointer, and `git worktree` checkouts use
#   similar relative pointers back to the main working tree). Mounting only the
#   repo directory breaks those pointers; mounting the whole workspace root at
#   an identical path means every relative (and absolute) git pointer resolves
#   exactly as it does on the host, with zero path translation.
#
# HOW TO REMOVE EVERYTHING
#   scripts/ci-parity.sh clean
#
# WARM RUNTIME (measured): with the image built and volumes populated once,
# `test` completes in ~60s -- dependency install is a no-op cache hit (~2ms),
# so that time is almost entirely pytest actually rendering real clips through
# ffmpeg, the same cost a bare `uv run pytest -q` pays on any machine. Cold
# (first run: image build + full dependency install) is ~2-3 minutes.
# `typecheck` and `prek` are ~15-20s warm (no ffmpeg rendering in that path).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="${VID_REPO_DIR:-$DEFAULT_REPO_DIR}"
VOL_SUFFIX="${VID_VOL_SUFFIX:-main}"
WORKSPACE_ROOT="$(dirname "$REPO_DIR")"

IMAGE="vid-ci-parity"
DOCKERFILE="$DEFAULT_REPO_DIR/docker/ci-parity.Dockerfile"
VOL_UV_CACHE="vid-ci-parity-uv-cache-${VOL_SUFFIX}"
VOL_VENV_TEST="vid-ci-parity-venv-test-${VOL_SUFFIX}"
VOL_VENV_DEV="vid-ci-parity-venv-dev-${VOL_SUFFIX}"

MODE="${1:-all}"

build_image() {
  docker build -q -t "$IMAGE" -f "$DOCKERFILE" "$DEFAULT_REPO_DIR" >/dev/null
}

run_in_container() {
  # $1: the shell command to run inside the container.
  docker run --rm \
    -v "$WORKSPACE_ROOT:$WORKSPACE_ROOT" \
    -v "$VOL_UV_CACHE:/root/.cache/uv" \
    -v "$VOL_VENV_TEST:$REPO_DIR/.venv" \
    -v "$VOL_VENV_DEV:$REPO_DIR/.venv-ci-parity-dev" \
    -w "$REPO_DIR" \
    "$IMAGE" \
    bash -euo pipefail -c "$1"
}

# Mirrors the `test` job in .github/workflows/ci.yml step for step: install
# ffmpeg (baked into the image instead, same apt package), create the
# environment the same way (plain venv + editable install + pytest/ruff,
# NOT the full dev extras -- CI's test job does not install them either),
# assert no provider credentials leaked in, then lint and test.
CMD_TEST_JOB=$(cat <<'EOF'
echo "--- ffmpeg ---"
ffmpeg -version | head -1
# The venv volume persists across runs (that's the point -- a warm run does
# not reinstall the world), so only create it the first time.
[ -x .venv/bin/python ] || uv venv .venv
uv pip install --python .venv/bin/python -e .
uv pip install --python .venv/bin/python pytest ruff
echo "--- lint and format check ---"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
echo "--- provider credential leak check ---"
leaked=""
for var in ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY GEMINI_API_KEY AZURE_OPENAI_API_KEY GH_TOKEN; do
  if [ -n "${!var:-}" ]; then leaked="$leaked $var"; fi
done
if [ -n "$leaked" ]; then
  echo "Provider credentials present in container:$leaked"
  exit 1
fi
echo "no provider credentials -- deterministic claims mean something"
echo "--- pytest -q ---"
.venv/bin/python -m pytest -q
EOF
)

# Mirrors the `type-check` job: full extras/groups, ty against the same
# concise/no-progress invocation CI uses.
CMD_TYPECHECK_JOB=$(cat <<EOF
export UV_PROJECT_ENVIRONMENT="$REPO_DIR/.venv-ci-parity-dev"
uv sync --frozen --all-extras --all-groups
uv run ty check --output-format concise --no-progress .
EOF
)

# Mirrors CONTRIBUTING.md's local gate: `prek run --all-files`, against the
# same full dev environment (`uv sync --frozen --all-extras --all-groups`)
# .pre-commit-config.yaml's hooks expect.
CMD_PREK=$(cat <<EOF
export UV_PROJECT_ENVIRONMENT="$REPO_DIR/.venv-ci-parity-dev"
uv sync --frozen --all-extras --all-groups
git config --global --add safe.directory "$REPO_DIR"
prek run --all-files
EOF
)

case "$MODE" in
  test)
    build_image
    run_in_container "$CMD_TEST_JOB"
    ;;
  typecheck)
    build_image
    run_in_container "$CMD_TYPECHECK_JOB"
    ;;
  prek)
    build_image
    run_in_container "$CMD_PREK"
    ;;
  all)
    build_image
    run_in_container "$CMD_PREK"
    run_in_container "$CMD_TYPECHECK_JOB"
    run_in_container "$CMD_TEST_JOB"
    ;;
  shell)
    build_image
    docker run --rm -it \
      -v "$WORKSPACE_ROOT:$WORKSPACE_ROOT" \
      -v "$VOL_UV_CACHE:/root/.cache/uv" \
      -v "$VOL_VENV_TEST:$REPO_DIR/.venv" \
      -v "$VOL_VENV_DEV:$REPO_DIR/.venv-ci-parity-dev" \
      -w "$REPO_DIR" \
      "$IMAGE" \
      bash
    ;;
  clean)
    docker volume rm -f "$VOL_UV_CACHE" "$VOL_VENV_TEST" "$VOL_VENV_DEV" >/dev/null 2>&1 || true
    docker image rm -f "$IMAGE" >/dev/null 2>&1 || true
    echo "removed image $IMAGE and volumes $VOL_UV_CACHE $VOL_VENV_TEST $VOL_VENV_DEV"
    ;;
  *)
    echo "usage: $0 [all|test|typecheck|prek|shell|clean]" >&2
    exit 2
    ;;
esac
