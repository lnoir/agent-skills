#!/usr/bin/env bash
# Benign installer: installs the skill's own declared deps from a local file.
# No global (-g) install, no remote fetch, no force-reinstall.
pip install -r requirements.txt
