"""Run externally configured local mine and notification commands."""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from config import load_config


def configured_command(value, field):
    if not isinstance(value, list) or not value or not all(isinstance(part, str) for part in value):
        raise ValueError(f"scheduler.{field} must be a non-empty argument array")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True); parser.add_argument("--agent")
    args = parser.parse_args(argv)
    config = load_config(args.config, args.agent)
    schedule = config.get("scheduler", {})
    command = configured_command(schedule.get("mine_command"), "mine_command")
    environment = {**os.environ, "COLLABORATION_RETRO_CONFIG": config["config_path"],
                   "COLLABORATION_RETRO_AGENT": config["agent"]}
    # Never inherit launchd's log streams or input, including in the notifier.
    streams = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL}
    result = subprocess.run(command, check=False, env=environment, **streams)
    notification = schedule.get("notification_command")
    if notification and result.returncode == 0:
        subprocess.run(configured_command(notification, "notification_command"),
                       check=False, env=environment, **streams)
    return result.returncode

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print("schedule: error: configuration or local command failed", file=sys.stderr); raise SystemExit(2)
