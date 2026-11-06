"""Sample app: a tiny web service that fetches data and makes a thumbnail."""

import os
import sys

import flask
import requests
from PIL import Image

from utils import load_config


def main():
    config = load_config()
    response = requests.get(config["url"], timeout=10)
    image = Image.open(os.path.join(sys.argv[1], "photo.jpg"))
    return flask.jsonify({"status": response.status_code, "size": image.size})


if __name__ == "__main__":
    main()
