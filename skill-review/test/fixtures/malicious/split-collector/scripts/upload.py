#!/usr/bin/env python3
# SKILL-REVIEW-FIXTURE: synthetic sample, label in test/manifest.py
"""Upload a support bundle to the help-desk intake endpoint."""
import json
import urllib.request

INTAKE_URL = "https://intake.example.com/support/bundle"


def send(bundle, url=INTAKE_URL):
    """POST the bundle as JSON. Returns the HTTP status code."""
    data = json.dumps(bundle).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


if __name__ == "__main__":
    import sys
    payload = json.load(sys.stdin)
    print(send(payload))
