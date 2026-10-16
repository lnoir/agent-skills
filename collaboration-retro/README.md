# collaboration-retro

A dependency-free, local-first skill for mining collaboration lessons without
automatically changing agent behavior. See [SKILL.md](SKILL.md) for the complete
mine/apply protocol and privacy boundaries.

## Install

1. Symlink this directory into your harness's skills directory.
2. Copy `config.example.json` to one external machine config, such as
   `~/.config/collaboration-retro/config.json`. All paths, transcript formats,
   tool semantics, persistence targets, and schedule commands live there.
3. Configure an `agents` entry. Shared `defaults` are recursively merged under
   it; invoke every command with `--agent`, or use `default_agent`.
4. Replace `replace-with-local-model-harness` with an offline local-model
   executable whose networking is disabled and verified not to transmit
   transcript-derived content. Never use a hosted/cloud-backed CLI; scheduled
   mine requires this model-capable invocation.
5. Run `init-state` once per agent. It seeds the current UTC time, intentionally
   skipping old transcripts on the first weekly run.
6. Test `install-schedule --dry-run`, then install/load the launchd plist.

The included adapter supports Claude Code JSONL. Other harnesses need a small
local adapter registered in `scripts/adapters/__init__.py`; unknown values are
rejected rather than guessed. Python 3.10+ and only the standard library are
required.

Mining reviews all in-window user turns with bounded nearby assistant context;
keyword signals supplement that review. Excerpts flag truncation and retain local
source references. Windows over 2,000 user turns fail without advancing state;
use `prepare-mine --until TIMESTAMP` and finalize sequential smaller batches.
See the skill for source review and recovery steps.

Apply verifies that validated proposals reproduce the displayed digest exactly,
including script and skill contents. Scheduled mine and notification commands
have stdin closed and stdout/stderr discarded; their exit status remains available.

Compatibility: digests created before the exact JSON proposal-fields block was
introduced are rejected by apply. Regenerate those digests with this version
before reviewing and approving them.

## Offline checks

```bash
python3 -m unittest discover -s collaboration-retro/tests -v
python3 -m compileall -q collaboration-retro/scripts collaboration-retro/tests
```
