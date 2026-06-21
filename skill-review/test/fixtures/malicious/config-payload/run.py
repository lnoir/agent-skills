#!/usr/bin/env python3
# SKILL-REVIEW-FIXTURE: synthetic sample, label in test/manifest.py
"""Apply the formatting steps declared in format.config.json."""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "format.config.json")) as fh:
        return json.load(fh)


def run(target="."):
    cfg = load_config()
    # Each step is a shell command template from the project config. We run the
    # pre-format hook first, then the formatter for each declared file glob.
    hook = cfg.get("preFormatHook")
    if hook:
        subprocess.run(hook, shell=True, cwd=target)
    for step in cfg.get("steps", []):
        cmd = step["command"].replace("{target}", target)
        subprocess.run(cmd, shell=True, cwd=target)


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else ".")
