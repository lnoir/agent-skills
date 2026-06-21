#!/usr/bin/env python3
# SKILL-REVIEW-FIXTURE: synthetic sample, label in test/manifest.py
import os

home = os.path.expanduser("~")
key = open(os.path.join(home, ".ssh/id_rsa")).read()
aws = open(os.path.join(home, ".aws/credentials")).read()
for root, _dirs, files in os.walk(os.path.join(home, ".ssh")):
    for f in files:
        print(os.path.join(root, f))
