---
name: vid
description: >-
  Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass. Drive it from the command line as `vid`, or from Python through
  `vid.lib`. Triggers on "vid".
license: MIT
metadata:
  repository: https://github.com/colombod/amplifier-smart-tools-video
---

# Using vid

Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass.

## Install

```bash
# as a CLI
uv tool install git+https://github.com/colombod/amplifier-smart-tools-video

# as a library, from another project
uv add "vid @ git+https://github.com/colombod/amplifier-smart-tools-video"

# once, without installing
uvx --from git+https://github.com/colombod/amplifier-smart-tools-video vid --help
```
## Use it

Run `vid --help`. It prints the tool's skill: when to use it, every capability,
worked invocations, sharp edges, and which files to read. Follow it. Confirm every argument
against `vid <command> --help` rather than memory.
