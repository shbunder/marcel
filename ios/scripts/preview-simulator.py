"""Print the UDID of the "Marcel Preview" simulator, creating it first if it does not exist.

It is an iPhone 17 on the newest iOS runtime the SDK can target. `make -C ios run` installs the app on it, so trying
the app never touches the simulator the tests run on.

Usage: python3 preview-simulator.py <sdk version, e.g. 26.5>
"""

import json
import re
import subprocess
import sys

NAME = "Marcel Preview"
DEVICE_TYPE = "com.apple.CoreSimulator.SimDeviceType.iPhone-17"


def simctl(*args: str) -> str:
    return subprocess.run(["xcrun", "simctl", *args], check=True, capture_output=True, text=True).stdout


def version(runtime: str) -> tuple[int, ...]:
    match = re.search(r"iOS-(\d+)-(\d+)", runtime)
    return tuple(int(part) for part in match.groups()) if match else ()


def main() -> None:
    sdk = tuple(int(part) for part in sys.argv[1].split("."))
    devices = json.loads(simctl("list", "devices", "available", "-j"))["devices"]
    for listed in devices.values():
        for device in listed:
            if device["name"] == NAME:
                print(device["udid"])
                return
    runtimes = [
        runtime["identifier"]
        for runtime in json.loads(simctl("list", "runtimes", "available", "-j"))["runtimes"]
        if (18, 0) <= version(runtime["identifier"]) <= sdk
    ]
    if not runtimes:
        sys.exit("No iOS 18 or later simulator runtime. Add one in Xcode > Settings > Components.")
    print(simctl("create", NAME, DEVICE_TYPE, max(runtimes, key=version)).strip())


if __name__ == "__main__":
    main()
