"""Print the UDID of an available iPhone simulator to test on: the newest iOS runtime the SDK can target.

Usage: xcrun simctl list devices available -j | python3 pick-simulator.py <sdk version, e.g. 26.5>
"""

import json
import re
import sys


def version(runtime: str) -> tuple[int, ...]:
    match = re.search(r"iOS-(\d+)-(\d+)", runtime)
    return tuple(int(part) for part in match.groups()) if match else ()


def main() -> None:
    sdk = tuple(int(part) for part in sys.argv[1].split("."))
    devices = json.load(sys.stdin)["devices"]
    candidates = [
        (version(runtime), device["name"], device["udid"])
        for runtime, listed in devices.items()
        if (18, 0) <= version(runtime) <= sdk
        for device in listed
        if device.get("isAvailable", True) and device["name"].startswith("iPhone")
    ]
    if not candidates:
        sys.exit("No iPhone simulator with iOS 18 or later. Add one in Xcode > Settings > Components.")
    newest = max(runtime for runtime, _, _ in candidates)
    print(sorted(c for c in candidates if c[0] == newest)[0][2])


if __name__ == "__main__":
    main()
