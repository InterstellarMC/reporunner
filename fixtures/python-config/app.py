import json
from pathlib import Path
def multiply(value):
    config = json.loads(Path(__file__).with_name("config.json").read_text())
    if not isinstance(config["multiplier"], int):
        raise TypeError("config.multiplier must be integer")
    return value * config["multiplier"]
