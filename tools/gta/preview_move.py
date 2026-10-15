"""Publish a one-second planar CJ movement proof to an explicitly enabled adapter."""
from __future__ import annotations
import argparse
import json
import math
import struct
import time
from native.inspect_state import Observer
from preview_hold import scene, packet as hold_packet
from preview_pose import Publisher

BODY = struct.Struct("<8s4I4Q4I7fI")
assert BODY.size == 104


def packet(state, identity, request, sequence, now_ms, duration_ms, position, heading, velocity):
    if not 0 < duration_ms <= 1000:
        raise ValueError("Command proof must end within 1000 ms")
    # Reuse the exact existing identity/foreground/grounded/heartbeat preflight.
    prefix = hold_packet(state, identity, request, sequence, now_ms, duration_ms)
    if (len(position) != 3 or len(velocity) != 3
            or any(not math.isfinite(v) or abs(v) > 100000 for v in position)
            or not math.isfinite(heading) or abs(heading) > 32
            or any(not math.isfinite(v) for v in velocity)
            or math.hypot(*velocity) > .020000001 or velocity[2] != 0):
        raise ValueError("Finite planar GTA coordinates/yaw and raw speed <= 0.02 required")
    fields = list(struct.unpack("<8s4I4Q4I", prefix))
    fields[0], fields[2] = b"GTAMOVE1", BODY.size
    return BODY.pack(*fields, *position, heading, *velocity, 0)


def trajectory(initial, elapsed, seconds, speed, yaw_degrees):
    if (not all(math.isfinite(v) for v in (elapsed, seconds, speed, yaw_degrees))
            or not .1 <= seconds <= 1 or not 0 <= elapsed <= seconds
            or not 0 <= speed <= 1 or abs(yaw_degrees) > 90*seconds):
        raise ValueError("Bounded path requires <= 1 s, 1 m/s and 90 degrees/s")
    heading = initial["heading"]
    forward = (-math.sin(heading), math.cos(heading), 0.)
    position = [p + axis*speed*elapsed for p, axis in zip(initial["position"], forward)]
    velocity = [axis*speed/50 for axis in forward]
    return position, heading + math.radians(yaw_degrees)*elapsed/seconds, velocity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=1)
    parser.add_argument("--speed", type=float, default=.25, help="GTA metres/second, maximum 1")
    parser.add_argument("--yaw-degrees", type=float, default=0, help="Total heading change during the proof")
    args = parser.parse_args()
    try:
        if not 0 < args.pid <= 0xffffffff:
            raise ValueError("Positive PID required")
        trajectory(dict(position=[0., 0., 0.], heading=0.), 0, args.seconds, args.speed, args.yaw_degrees)
    except ValueError as exc:
        parser.error(str(exc))
    observer, publisher = Observer(args.pid), None
    count, request = 0, 0
    started = time.monotonic()
    try:
        first = observer.read()
        identity = scene(first)
        publisher = Publisher(observer, transport="MOVE_V1", packet_size=BODY.size)
        request = observer.api.GetTickCount64()
        started = time.monotonic()
        while (elapsed := time.monotonic()-started) < args.seconds:
            latest = observer.read()
            position, heading, velocity = trajectory(first, elapsed, args.seconds, args.speed, args.yaw_degrees)
            count += 1
            publisher.publish(packet(latest, identity, request, count, observer.api.GetTickCount64(),
                                     round(args.seconds*1000), position, heading, velocity), count)
            time.sleep(max(0, min(1/60, started+count/60-time.monotonic())))
    finally:
        if publisher:
            publisher.close()
        observer.close()
    print(json.dumps(dict(format="gta-thaw-movement-publisher-v1", request_id=request, published=count,
                         elapsed_seconds=time.monotonic()-started, packet_cleared=True,
                         native_acceptance="check adapter counters and independent observed motion",
                         gameplay_enabled=False)))


if __name__ == "__main__":
    main()
