#!/usr/bin/env python3
# SKILL-REVIEW-FIXTURE: synthetic sample, label in test/manifest.py
"""Local logging helper. Writes only to a local log; suppresses stderr."""
import subprocess
import sys


def run_logged(cmd):
    with open("run.log", "a") as log:
        # Discard stderr noise.
        subprocess.run(cmd + " 2>/dev/null", shell=False, stdout=log)


if __name__ == "__main__":
    run_logged(sys.argv[1] if len(sys.argv) > 1 else "echo hello")
