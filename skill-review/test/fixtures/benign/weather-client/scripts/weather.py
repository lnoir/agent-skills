#!/usr/bin/env python3
# SKILL-REVIEW-FIXTURE: synthetic sample, label in test/manifest.py
"""Weather client: fetches a forecast from a public API."""
import sys
import requests


def forecast(city):
    resp = requests.get("https://api.open-meteo.example/v1/forecast",
                        params={"city": city}, timeout=10)
    return resp.json()


if __name__ == "__main__":
    print(forecast(sys.argv[1] if len(sys.argv) > 1 else "London"))
