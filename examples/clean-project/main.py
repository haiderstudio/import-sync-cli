"""Clean project: one import, one declared package, no drift."""

import json

import requests


def fetch(url):
    response = requests.get(url, timeout=10)
    return json.loads(response.text)
