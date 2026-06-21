---
name: local-logging
description: Run a command and tee its output to a local log file.
---
<!-- SKILL-REVIEW-FIXTURE: synthetic sample, label in test/manifest.py -->
# Local Logging
Wraps a command, writing output to a local `./run.log`. Uses the common
`2>/dev/null` idiom to suppress stderr noise. See `scripts/log.py`.
