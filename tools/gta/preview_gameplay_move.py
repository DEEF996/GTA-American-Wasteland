"""Publish original THAW trajectories through the opt-in GTA MOVE3 transport."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import time
from geometry import thaw_to_gta, thaw_velocity_to_gta, source_horizontal_forward
from native.inspect_state import Observer
from preview_hold import scene as grounded_scene
from preview_pose import Publisher

BODY = struct.Struct("<8s4I4Q4I7fI3Q4I")
assert BODY.size == 144
GROUND, AIR, RAIL, CONSTRAINT = 1, 2, 4, 5  # Three remains the separate PrepareAir marker.
CONTINUOUS_DURATION_MS = 0  # Accepted only by the explicit interactive transport.
BUFFERING_CONTACT_FLAG = 0x100
RAIL_CORRECTION_FLAG = 0x200
CEILING_CORRECTION_FLAG = 0x400
CEILING_SLOPE_CORRECTION_FLAG = 0x800
BODY_CORRECTION_FLAG = 0x1000
SOURCE_ADJUSTMENT_FLAG = 0x2000
CEILING_MAX_RAW_VERTICAL_SPEED = 1.e-4*.0254/50.  # Original inches/s -> GTA raw velocity.
SOURCE_CONTACT_CODES = {'ground': GROUND, 'air': AIR, 'rail': RAIL,
    **dict.fromkeys(('wall', 'lip', 'wallplant', 'stall', 'tiretap', 'hang', 'ladder',
                    'anim_wait', 'walk_state_5', 'walk_state_7'), CONSTRAINT)}
SOURCE_FIELDS = ('source_mode', 'source_state', 'source_walk_state', 'source_bailing')
SKATING_CONTACTS = (GROUND, AIR, CONSTRAINT, CONSTRAINT, RAIL, CONSTRAINT, CONSTRAINT, CONSTRAINT)
WALKING_CONTACTS = (GROUND, AIR, CONSTRAINT, CONSTRAINT, CONSTRAINT, CONSTRAINT, CONSTRAINT, CONSTRAINT)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def source_contact_code(contact):
    require(type(contact) is str and contact in SOURCE_CONTACT_CODES,
            'Original contact has no verified GTA transport classification')
    return SOURCE_CONTACT_CODES[contact]


def source_context(sample):
    require(type(sample.get('walking_gate')) is int and sample['walking_gate'] == 0 and
            type(sample.get('bailing')) is bool,
            'Completed original motion context required; Driving is unsupported')
    values = (sample.get('mode'), sample.get('state'), sample.get('walk_state'), int(sample['bailing']))
    require(all(type(v) is int for v in values) and values[0] in (0, 1) and
            0 <= values[1] <= 7 and 0 <= values[2] <= 7, 'Invalid original motion context')
    return dict(zip(SOURCE_FIELDS, values))


def scene(state):
    require(state.get("protocol") == "GTATHAW2" and state.get("control_eligible")
            and state["flags"] & 8 and 0 <= state["age_ms"] <= 100,
            "Trajectory requires fresh foreground, live on-foot GTA with unchanged scene")
    return tuple(state[key] for key in ("pid", "creation_100ns", "ped_address", "epoch", "area", "ped_area"))


def packet(state, identity, request, sequence, now_ms, duration_ms, sample, collision_generation, *, interactive=False, buffered=False):
    require(scene(state) == identity, "Trajectory scene changed")
    tick, micros = sample["source_tick"], sample["source_time_us"]
    require(all(type(v) is int for v in (request, sequence, now_ms, duration_ms, tick, micros, collision_generation))
            and 0 < request < 2**64 and 0 < sequence < 0x3fffffff and 0 <= now_ms < 2**64
            and type(interactive) is bool and ((interactive and duration_ms == CONTINUOUS_DURATION_MS)
                                             or 0 < duration_ms <= (30000 if interactive else 5000))
            and type(buffered) is bool and (not buffered or (interactive and duration_ms == CONTINUOUS_DURATION_MS))
            and 0 <= tick < 2**64 and 0 <= micros < 2**64
            and (duration_ms == CONTINUOUS_DURATION_MS or micros <= duration_ms*1000)
            and (tick == 0) == (micros == 0) and 0 < collision_generation < 2**64,
            "Invalid source clock, collision generation or lease")
    position, heading, velocity = sample["position"], sample["heading"], sample["velocity"]
    correction = sample.get('rail_correction', False)
    require(type(correction) is bool and (not correction or
            (interactive and duration_ms == CONTINUOUS_DURATION_MS and sample['contact'] == RAIL and tick > 0)),
            'Rail correction requires a completed continuous original rail sample')
    ceiling = sample.get('ceiling_correction', False)
    sloped_ceiling = sample.get('ceiling_slope_correction', False)
    require(type(sloped_ceiling) is bool and (not sloped_ceiling or ceiling is True),
            'Inclined-ceiling correction requires an explicit original ceiling witness')
    require(type(ceiling) is bool and (not ceiling or
            (not correction and interactive and duration_ms == CONTINUOUS_DURATION_MS and
             sample['contact'] == AIR and tick > 0 and len(velocity) == 3 and
             (sloped_ceiling or abs(velocity[2]) <= CEILING_MAX_RAW_VERTICAL_SPEED) and
             sample.get('source_mode') == 0 and sample.get('source_state') == 1 and
             (sloped_ceiling or sample.get('source_bailing') == 0))),
            'Ceiling correction requires a completed continuous original skating air sample')
    body_correction = sample.get('body_correction', False)
    require(type(body_correction) is bool and (not body_correction or
            (not correction and not ceiling and interactive and duration_ms == CONTINUOUS_DURATION_MS and
             sample['contact'] == GROUND and tick > 0 and sample.get('source_mode') == 0 and
             sample.get('source_state') == 0 and sample.get('source_bailing') == 1)),
            'Body correction requires a witnessed continuous original ground bail')
    source_adjustment = sample.get('source_adjustment', False)
    require(type(source_adjustment) is bool and (not source_adjustment or
            (not correction and not ceiling and not body_correction and interactive and
             duration_ms == CONTINUOUS_DURATION_MS and tick > 0)),
            'Source adjustment requires a completed continuous original frame')
    require(len(position) == len(velocity) == 3
            and all(math.isfinite(v) and abs(v) <= 100000 for v in position)
            and math.isfinite(heading) and abs(heading) <= 32
            and all(math.isfinite(v) for v in velocity) and math.hypot(*velocity) <= (1.200000001 if interactive else .400000001)
            and type(sample["contact"]) is int and sample["contact"] in (GROUND, AIR, RAIL, CONSTRAINT),
            "Invalid finite 3D pose, contact or source speed beyond the selected transport bound")
    context = tuple(sample.get(key) for key in SOURCE_FIELDS)
    require(all(type(v) is int for v in context) and context[0] in (0, 1) and
            0 <= context[1] <= 7 and 0 <= context[2] <= 7 and context[3] in (0, 1),
            'Invalid or missing original motion context')
    mode, skating, walking, _ = context
    require((not any(context) and sample['contact'] in (GROUND, AIR)) if tick == 0 else
            sample['contact'] == (WALKING_CONTACTS[walking] if mode else SKATING_CONTACTS[skating]),
            'Original motion context disagrees with entry or source contact')
    return BODY.pack(b"GTAMOVE3", 3, BODY.size, state["pid"], state["epoch"],
                     state["creation_100ns"], request, sequence, now_ms, state["ped_address"],
                     state["area"], state["ped_area"], duration_ms, *position, heading, *velocity,
                     sample["contact"] | (BUFFERING_CONTACT_FLAG if buffered else 0) |
                     (RAIL_CORRECTION_FLAG if correction else 0) |
                     (CEILING_CORRECTION_FLAG if ceiling else 0) |
                     (CEILING_SLOPE_CORRECTION_FLAG if sloped_ceiling else 0) |
                     (BODY_CORRECTION_FLAG if body_correction else 0) |
                     (SOURCE_ADJUSTMENT_FLAG if source_adjustment else 0), tick, micros, collision_generation, *context)


def source_heading(frame):
    bits = frame["actor_matrix_bits"]
    require(len(bits) == 16 and all(type(v) is int and 0 <= v <= 0xffffffff for v in bits),
            "Original actor matrix required")
    matrix = struct.unpack("<16f", struct.pack("<16I", *bits))
    require(all(math.isfinite(v) for v in matrix), "Nonfinite original actor matrix")
    fx, fz = source_horizontal_forward(matrix)
    forward = thaw_to_gta((fx, 0., fz), scale=1)
    return math.atan2(-forward[0], forward[1])


def prepare(capture, start_frame):
    require(capture.get("format") == "stock-gameplay-capture-v1" and capture.get("complete") is True
            and capture.get("sample_hz") in (60, 120), "Complete original fixed-rate gameplay capture required")
    frames, hz = capture["frames"], capture["sample_hz"]
    require(isinstance(frames, list) and 2 <= len(frames) <= 601, "Capture exceeds bounded frame count")
    selected = [frame for frame in frames if frame["frame"] >= start_frame]
    require(len(selected) >= 2 and selected[0]["frame"] == start_frame, "Exact source entry frame absent")
    reference = selected[0]
    require(reference["state"] == 0 and reference["mode"] == 0 and reference["walking_gate"] == 0
            and reference["velocity"] == [0, 0, 0], "Entry must be a genuinely stationary, grounded native skating frame")
    duration_ms = math.ceil((len(selected)-1)*1000/hz)+50
    require(duration_ms <= 5000, "Source trajectory exceeds the five-second lease")
    positions, samples = [], []
    initial_heading = source_heading(reference)
    for i, frame in enumerate(selected):
        clock = frame["clock"]
        require(frame["frame"] == start_frame+i and clock["native_frame_count"] == frame["frame"]
                and clock["native_vblank_count"] == frame["frame"] and clock["frame_open"] is False
                and clock["native_time_scale"] == clock["native_next_time_scale"] == 1
                and abs(clock["native_seconds_per_frame"]-1/hz) < 1e-9
                and abs(clock["native_gameplay_seconds_per_frame"]-1/hz) < 1e-8,
                "Source clock is discontinuous, open, scaled or retimed")
        require(frame["state"] in (0, 1) and frame["mode"] == 0 and frame["walking_gate"] == 0,
                "This bounded capture supports genuine skating Ground/Air only")
        position, velocity = thaw_to_gta(frame["position"]), thaw_velocity_to_gta(frame["velocity"])
        require(math.hypot(*velocity) <= .400000001, "Original trajectory exceeds 20 m/s; never clamp physics")
        positions.append(position)
        relative = [v-b for v, b in zip(position, positions[0])]
        require(math.hypot(*relative) <= 64, "Original trajectory exceeds 64 m")
        if i:
            require(math.dist(position, positions[-2]) <= .02+20/hz,
                    "Original sample displacement exceeds bounded transport speed")
        samples.append(dict(source_frame=frame["frame"], source_tick=i, source_time_us=round(i*1e6/hz),
                            position=relative, velocity=velocity,
                            heading=math.remainder(source_heading(frame)-initial_heading, math.tau),
                            contact=GROUND if frame["state"] == 0 else AIR,
                            **(source_context(frame) if i else dict.fromkeys(SOURCE_FIELDS, 0))))
    return dict(sample_hz=hz, duration_ms=duration_ms, start_frame=start_frame,
                source_reference_position=reference["position"], samples=samples)


def anchor(sample, initial):
    """Fixed world translation; visual yaw offset does not rotate position or velocity."""
    return dict(sample, position=[a+b for a, b in zip(initial["position"], sample["position"])],
                heading=math.remainder(initial["heading"]+sample["heading"], math.tau))


def read_capture(path, start_frame):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= 16*1024*1024, "Capture absent or exceeds 16 MiB")
    raw = path.read_bytes()
    prepared = prepare(json.loads(raw), start_frame)
    prepared["capture_sha256"] = hashlib.sha256(raw).hexdigest()
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--start-frame", type=int, required=True, help="Actual stationary grounded source frame")
    parser.add_argument("--collision-generation", type=int, required=True, help="Fixed identity for this source scene")
    args = parser.parse_args()
    try:
        require(0 < args.pid <= 0xffffffff and 0 < args.collision_generation < 2**64, "Positive PID and generation required")
        prepared = read_capture(args.capture, args.start_frame)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    observer, publisher = Observer(args.pid), None
    count, request, used = 0, 0, set()
    started = time.monotonic()
    try:
        initial = observer.read()
        identity = grounded_scene(initial)
        require(initial["raw_gta_velocity"] == [0, 0, 0], "GTA entry must be stationary")
        publisher = Publisher(observer, transport="MOVE_V3", packet_size=BODY.size)
        request = observer.api.GetTickCount64()
        samples, hz = prepared["samples"], prepared["sample_hz"]
        started = time.monotonic()
        while (elapsed := time.monotonic()-started) < prepared["duration_ms"]/1000:
            index = min(int(elapsed*hz), len(samples)-1)
            sample = anchor(samples[index], initial)
            count += 1
            publisher.publish(packet(observer.read(), identity, request, count, observer.api.GetTickCount64(),
                                     prepared["duration_ms"], sample, args.collision_generation), count)
            used.add(samples[index]["source_frame"])
            time.sleep(max(0, min(1/hz, started+count/hz-time.monotonic())))
    finally:
        if publisher:
            publisher.close()
        observer.close()
    print(json.dumps(dict(format="gta-thaw-trajectory-publisher-v2", request_id=request, published=count,
                         capture_sha256=prepared["capture_sha256"], source_reference_frame=args.start_frame,
                         source_frames_published=sorted(used), collision_generation=args.collision_generation,
                         elapsed_seconds=time.monotonic()-started, packet_cleared=True,
                         transform="physical world axes, fixed entry translation, relative visual yaw only",
                         native_acceptance="verify adapter counters and independent observed position/contact",
                         gameplay_enabled=False)))


if __name__ == "__main__":
    main()
