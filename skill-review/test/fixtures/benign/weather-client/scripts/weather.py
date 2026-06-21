#!/usr/bin/env python3
"""Benign weather client: documented network use, the skill's stated purpose."""
import sys
import requests


def forecast(city):
    resp = requests.get("https://api.open-meteo.example/v1/forecast",
                        params={"city": city}, timeout=10)
    return resp.json()


if __name__ == "__main__":
    print(forecast(sys.argv[1] if len(sys.argv) > 1 else "London"))
