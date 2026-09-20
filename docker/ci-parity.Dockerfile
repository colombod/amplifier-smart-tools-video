# CI-parity image: same base and same ffmpeg source as `.github/workflows/ci.yml`.
#
# `ubuntu-latest` on GitHub-hosted runners is Ubuntu 24.04 as of this writing, and
# every CI job installs ffmpeg with `apt-get install -y ffmpeg` rather than a
# newer build. That single fact is what the dev box cannot see on its own --
# this machine's ffmpeg is a bleeding-edge nightly, and a filter graph the
# nightly accepts can be rejected outright by the distro's stable ffmpeg
# (see: commit bfeaa13, exit 234 on 6.1.1). This image exists to put a real
# ubuntu-latest-shaped ffmpeg between "it passed on my machine" and "it passed".
#
# Deliberately thin: OS + ffmpeg + uv + prek. Project dependencies are NOT
# baked in here -- they are installed at container-run time, into a named
# volume, the same way CONTRIBUTING.md's setup and the CI workflow's own
# steps do it. That keeps this image cache-stable (it only needs rebuilding
# if the OS/ffmpeg/uv/prek layer itself changes) while every actual dependency
# version still comes from this repo's own pyproject.toml / uv.lock, never
# from something frozen into the image.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# git: uv sync inspects the workspace as a git repo; also matches actions/checkout
# being present in every CI job. ca-certificates/curl: fetch uv and prek.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Same installer astral-sh/setup-uv uses under the hood.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Baked into the image rather than left to download on first container run:
# the interpreter this project requires (pyproject.toml: requires-python
# >=3.13). Container-run state (the venvs) lives in named volumes that outlive
# any one `docker run`, but a bare-metal uv-managed interpreter download does
# not persist the same way unless /root/.local/share/uv is itself a volume --
# simplest to make it part of the image instead, so every run has it already.
RUN uv python install 3.13

# The local gate CONTRIBUTING.md documents (`prek run --all-files`) needs prek
# itself present, the same way a contributor's machine has it.
RUN uv tool install prek

WORKDIR /repo
