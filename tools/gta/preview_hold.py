"""Request a native, at-most-two-second idle-CJ hold; no position commands or skating."""
from __future__ import annotations
import argparse
import json
import math
import struct
import time
from native.inspect_state import Observer
from preview_pose import Publisher

BODY = struct.Struct("<8s4I4Q4I")
assert BODY.size == 72


def scene(state):
    if (state.get("protocol") != "GTATHAW2" or not state.get("control_eligible")
            or not state.get("grounded") or not state["flags"] & 8 or state["age_ms"] > 100):
        raise ValueError("Hold requires fresh foreground, idle on-foot GTA; native task/input gates also apply")
    return tuple(state[key] for key in ("pid", "creation_100ns", "ped_address", "epoch", "area", "ped_area"))


def packet(state, identity, request, sequence, now_ms, duration_ms):
    if scene(state) != identity:
        raise ValueError("Hold scene changed")
    if not (0 < request <= 0xffffffffffffffff and 0 < sequence < 0x3fffffff and 0 < duration_ms <= 2000):
        raise ValueError("Invalid bounded hold request")
    return BODY.pack(b"GTAHOLD1", 1, BODY.size, state["pid"], state["epoch"],
                     state["creation_100ns"], request, sequence, now_ms,
                     state["ped_address"], state["area"], state["ped_area"], duration_ms)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=1)
    args = parser.parse_args()
    if not 0 < args.pid <= 0xffffffff or not math.isfinite(args.seconds) or not .05 <= args.seconds <= 2:
        parser.error("positive PID and 0.05 <= seconds <= 2 required")
    observer, publisher = Observer(args.pid), None
    count, request = 0, 0
    started = time.monotonic()
    try:
        first = observer.read()
        identity = scene(first)
        publisher = Publisher(observer, transport="HOLD_V1", packet_size=BODY.size)
        request = observer.api.GetTickCount64()
        started = time.monotonic()
        while time.monotonic()-started < args.seconds:
            latest = observer.read()
            count += 1
            publisher.publish(packet(latest,identity,request,count,observer.api.GetTickCount64(),
                                     round(args.seconds*1000)),count)
            time.sleep(max(0, min(1/60, started+count/60-time.monotonic())))
    finally:
        if publisher:
            publisher.close()
        observer.close()
    print(json.dumps(dict(format="gta-thaw-hold-publisher-v1", request_id=request, published=count,
        elapsed_seconds=time.monotonic()-started, maximum_native_hold_ms=round(args.seconds*1000),
        packet_cleared=True, gameplay_enabled=False, native_acceptance="check adapter-status.json")))


if __name__ == "__main__":
    main()
