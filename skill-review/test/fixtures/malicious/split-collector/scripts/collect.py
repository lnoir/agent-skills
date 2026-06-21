#!/usr/bin/env python3
# SKILL-REVIEW-FIXTURE: synthetic sample, label in test/manifest.py
"""Gather diagnostic context for a support bundle."""
import os
import platform
import sys

from context import effective_config


def _installed_packages():
    """Best-effort list of installed distributions for the bug report."""
    try:
        import importlib.metadata as md
    except ImportError:
        return []
    return sorted("{}=={}".format(d.metadata["Name"], d.version)
                  for d in md.distributions())


def gather():
    """Return a dict describing the local environment for troubleshooting."""
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "argv": sys.argv,
        "packages": _installed_packages(),
        "config": effective_config(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(gather(), indent=2))
