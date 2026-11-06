"""Sample helper: loads settings from a YAML file."""

import yaml

import numpy as np

from app import helper


def load_config():
    with open("config.yaml", encoding="utf-8") as handle:
        settings = yaml.safe_load(handle)
    settings["weights"] = np.array(settings["weights"])
    return helper(settings)
