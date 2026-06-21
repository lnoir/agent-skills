#!/usr/bin/env python3
"""Benign git helper: stage and commit using argv lists (no shell=True)."""
import subprocess
import sys


def commit(message):
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)


if __name__ == "__main__":
    commit(sys.argv[1] if len(sys.argv) > 1 else "chore: update")
