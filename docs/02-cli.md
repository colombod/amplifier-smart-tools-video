# CLI Reference

The CLI is a thin wrapper over the [library](01-library.md): one command per capability, taking the same arguments under the same names, and doing nothing the library does not. 
What each argument means and what a capability returns or raises is documented there. This page covers only what the CLI adds: the invocation shape, and what reaches stdout, stderr, and the exit code.

Results go to stdout and diagnostics to stderr. A failure the library can name prints its message to stderr and exits 1; a bad invocation exits 2.

## Help

```
vid -h                 terse summary for a person: the commands, a line each
vid --help             the tool's skill, written for an agent driving it
vid <command> --help   one command in full: arguments, defaults, exit codes
```

`--help` on the tool prints what `lib.skill()` returns; the CLI adds nothing of its own. Every command answers both `-h` and `--help` with the same per-command help.

## vid manifest

```bash
vid manifest
```

`lib.load_manifest()`, printed as JSON.

## Per-command reference

**It is `vid <command> --help`, and deliberately not this page.**

All 25 capabilities carry a full agent-facing document -- arguments, defaults, a worked
invocation, the result, and the failures -- generated at runtime by `vid.verbdoc`. The
library signatures in it are produced from `inspect.signature`, so they cannot drift from
the functions they describe, and tests assert that every exposed command has one and that
the rendered text matches the real signature.

This page used to claim "each command gets a section here". One of twenty-five did. The
honest fix is not to write the other twenty-four: a hand-maintained second copy of
generated, tested documentation is a copy that rots, and this repository has spent a
release learning that. So the per-command reference lives where it is checked, and this
page says where to find it.

```bash
vid --help             the tool's skill, for an agent driving it
vid <command> --help   one capability in full
```

## Adding a command

Give it an entry in `vid.verbdoc`, which is where its `--help` comes from. Nothing needs
to be added to this page.
