"""Read-only, bounded comparison of live GTA position and local static geometry.

This never sends controls, changes GTA memory, or starts a THAW runtime. A
successful lookup proves only that the imported static catalogue can be queried
for a fresh isolated GTA scene; it does not certify playable collision coverage.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sqlite3
import time

from geometry import SCHEMA, query_world
from native.inspect_state import Observer


MAX_SECONDS = 60.0
MAX_RADIUS = 512.0
MAX_AGE_MS = 1000.0
POLL_SECONDS = .5
PED_PRESENT, PAUSED, IN_VEHICLE, ON_FOOT, READ_FAULT = 1, 2, 4, 16, 32
SCENE_FIELDS = ("pid", "creation_100ns", "ped_address", "epoch", "area", "ped_area")


class Rejected(ValueError):
    pass


def validate_options(pid, seconds, radius):
    if type(pid) is not int or not 0 < pid <= 0xffffffff:
        raise ValueError("--pid must be a positive Windows process id")
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_SECONDS:
        raise ValueError("--seconds must be finite and within 0..60")
    if not math.isfinite(radius) or not 0 < radius <= MAX_RADIUS:
        raise ValueError("--radius must be finite and within 0..512 GTA units")


def validate_snapshot(observer, state):
    if (state.get("protocol"), state.get("pid"), state.get("creation_100ns")) != (
            "GTATHAW2", observer.pid, observer.created):
        raise Rejected("wrong_process_identity")
    if state.get("capabilities", 0) & 3 != 3:
        raise Rejected("missing_isolation_capability")
    profile = state.get("profile_path")
    expected = observer.exe.parent / "thaw_gta" / "profile"
    if not isinstance(profile, str) or not profile or "\0" in profile:
        raise Rejected("wrong_isolated_profile")
    profile = Path(profile)
    if not profile.is_absolute() or profile.resolve() != expected.resolve():
        raise Rejected("wrong_isolated_profile")
    age = state.get("age_ms")
    if not isinstance(age, (float, int)) or not math.isfinite(age) or not 0 <= age <= MAX_AGE_MS:
        raise Rejected("stale_snapshot")
    flags = state.get("flags", 0)
    if flags & READ_FAULT:
        raise Rejected("native_read_fault")
    if flags & PAUSED:
        raise Rejected("paused")
    if not flags & PED_PRESENT or not state.get("ped_address"):
        raise Rejected("no_ped")
    if flags & IN_VEHICLE or not flags & ON_FOOT:
        raise Rejected("not_on_foot")
    for key in ("epoch", "sequence"):
        if type(state.get(key)) is not int or state[key] <= 0:
            raise Rejected("invalid_scene_identity")
    for key in ("area", "ped_area"):
        if type(state.get(key)) is not int or not 0 <= state[key] <= 255:
            raise Rejected("invalid_area")
    if state["area"] != state["ped_area"]:
        raise Rejected("area_transition")
    for name, length, bound in (("position", 3, 100000), ("raw_gta_velocity", 3, 10000)):
        values = state.get(name)
        if not isinstance(values, (tuple, list)) or len(values) != length or not all(
                isinstance(v, (float, int)) and math.isfinite(v) and abs(v) <= bound for v in values):
            raise Rejected("invalid_player_coordinates")
    for name in ("heading", "health", "time_step"):
        value = state.get(name)
        if not isinstance(value, (float, int)) or not math.isfinite(value) or abs(value) > 100000:
            raise Rejected("invalid_player_state")
    if state["health"] <= 0:
        raise Rejected("ped_not_alive")
    for field, limit in (("clock_hour", 23), ("clock_minute", 59), ("clock_second", 60)):
        if type(state.get(field)) is not int or not 0 <= state[field] <= limit:
            raise Rejected("invalid_game_clock")
    for field, mask in (("controls_disabled_mask", 0xffff), ("cutscene_flags", 3), ("ped_motion_flags", 7)):
        if type(state.get(field)) is not int or state[field] < 0 or state[field] & ~mask:
            raise Rejected("invalid_control_or_motion_state")
    if state["cutscene_flags"]:
        raise Rejected("cutscene_active")
    if state["controls_disabled_mask"]:
        raise Rejected("controls_disabled")
    if state.get("control_eligible") is not True:
        raise Rejected("native_control_ineligible")


def snapshot_stamp(state):
    return {key: state[key] for key in SCENE_FIELDS}


def game_clock(state):
    return [state[key] for key in ("clock_hour", "clock_minute", "clock_second")]


def probe_once(observer, database, radius, *, query=query_world, clock=time.perf_counter, ipls=None):
    """Only counts survive the second state read; stale scene results are discarded."""
    started = clock()
    stage = "before_query"
    result = {"accepted": False, "physics_ready": False}
    try:
        validate_options(observer.pid, 0, radius)
        before = observer.read()
        validate_snapshot(observer, before)
        # Original GetIsTimeInRange compares only raw hours. Second 60 must not
        # activate the next hour's IDE models before GTA increments that field.
        hour = float(before["clock_hour"])
        result.update(scene=snapshot_stamp(before), sequence_begin=before["sequence"],
                      center=list(before["position"]), radius=radius,
                      clock_before=game_clock(before), query_hour=hour)
        stage = "before_ipls"
        activation = ipls.read() if ipls is not None else None
        stage = "query"
        lookup_started = clock()
        region = query(database, before["position"], radius, interior=before["area"],
                       active_streams=activation["active_streams"] if activation is not None else (),
                       hour=hour, include_dynamic=False,
                       max_instances=10000, max_primitives=250000)
        lookup_ms = (clock() - lookup_started) * 1000
        if ipls is not None:
            stage = "after_ipls"
            if activation != ipls.read():
                raise Rejected("ipl_changed_during_query")
        stage = "after_query"
        after = observer.read()
        validate_snapshot(observer, after)
        result["clock_after"] = game_clock(after)
        elapsed_ms = (clock() - started) * 1000
        if snapshot_stamp(before) != snapshot_stamp(after):
            raise Rejected("scene_changed_during_query")
        # IDE time windows use integer hours. Any changed hour could activate a
        # previously excluded model, so the next poll must query the new hour.
        if after["clock_hour"] != before["clock_hour"]:
            raise Rejected("time_window_changed_during_query")
        if after["sequence"] < before["sequence"] or after["game_timer"] < before["game_timer"] or after["frame_counter"] < before["frame_counter"]:
            raise Rejected("snapshot_clock_rolled_back")
        if elapsed_ms + before["age_ms"] > MAX_AGE_MS:
            raise Rejected("query_snapshot_expired")
        displacement = math.dist(before["position"], after["position"])
        if displacement > radius / 2:
            raise Rejected("player_left_query_margin")
        result.update(accepted=True, sequence_end=after["sequence"],
                      age_after_ms=after["age_ms"], query_ms=round(lookup_ms, 3),
                      control_eligible=True,
                      ped_motion_flags=after["ped_motion_flags"], grounded=bool(after["ped_motion_flags"] & 1),
                      elapsed_ms=round(elapsed_ms, 3),
                      player_displacement=round(displacement, 5),
                      counts=region["stats"], omitted=region["omitted"])
        if activation is not None:
            result["conditional_ipls"] = activation
    except Rejected as error:
        result.update(reason=str(error), stage=stage)
    except (ValueError, OSError, sqlite3.Error) as error:
        reason = ("geometry_query_error" if stage == "query" else
                  "ipl_read_error" if stage in ("before_ipls", "after_ipls") else "observer_read_error")
        result.update(reason=reason,
                      stage=stage, detail=str(error)[:300])
    return result


def summarize(samples):
    accepted = [sample for sample in samples if sample["accepted"]]
    query_times = [sample["query_ms"] for sample in accepted]
    counts = {}
    for key in ("instances", "models", "primitives"):
        values = [sample["counts"][key] for sample in accepted]
        counts[key] = {"minimum": min(values), "maximum": max(values)} if values else None
    return {"attempts": len(samples), "accepted": len(accepted),
            "rejections": dict(Counter(sample["reason"] for sample in samples if not sample["accepted"])),
            "areas": sorted({sample["scene"]["area"] for sample in accepted}),
            "clock_hours": sorted({sample["clock_before"][0] for sample in accepted}),
            "scene_count": len({tuple(sample["scene"][key] for key in SCENE_FIELDS) for sample in accepted}),
            "query_ms": {"minimum": min(query_times), "maximum": max(query_times),
                         "mean": round(sum(query_times)/len(query_times), 3)} if query_times else None,
            "counts": counts, "physics_ready": False}


def database_info(database):
    db = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        metadata = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM metadata")}
        if metadata.get("schema") != SCHEMA or metadata.get("complete") is not True:
            raise ValueError("Incomplete or unsupported geometry database")
        report = metadata["report"]
        size = report["sector_size"]
        if not isinstance(size, (float, int)) or not math.isfinite(size) or not 16 <= size <= 1024:
            raise ValueError("Invalid geometry database sector size")
        return {"schema": SCHEMA, "sector_size": size, "stats": report["stats"],
                "source_files": len(report["source_files"])}
    finally:
        db.close()


def run_probe(observer, database, seconds, radius, *, clock=time.monotonic, sleeper=time.sleep, ipls=None):
    validate_options(observer.pid, seconds, radius)
    info = database_info(database)
    started = clock()
    samples = []
    while True:
        sample = probe_once(observer, database, radius, ipls=ipls)
        if "center" in sample:
            sample["sector"] = [math.floor(v/info["sector_size"]) for v in sample["center"][:2]]
        samples.append(sample)
        remaining = seconds - (clock() - started)
        if remaining <= 0 or len(samples) >= 121:
            break
        sleeper(min(POLL_SECONDS, remaining))
    return {"format": "gta-thaw-static-world-probe-v2", "executable": str(observer.exe),
            "database": str(Path(database).resolve()), "requested_seconds": seconds,
            "database_info": info,
            "elapsed_seconds": round(clock()-started, 3), "radius": radius,
            "summary": summarize(samples), "samples": samples,
            "query_policy": {"geometry": "static IPL/COL catalogue only", "dynamic_entities": "excluded",
                             "conditional_ipls": ("read-only native pool + streaming state, checked before/after query"
                                                  if ipls is not None else "excluded; --live-ipls not enabled"),
                             "hour_source": "live GTATHAW2 raw hour, matching CClock::GetIsTimeInRange",
                             "hour_boundary": "discard crossing queries; next poll uses the new hour",
                             "control_eligibility": "native ControlEligible plus independently checked snapshot fields",
                             "maximum_snapshot_age_ms": MAX_AGE_MS,
                             "maximum_instances": 10000, "maximum_primitives": 250000},
            "physics_ready": False,
            "limitations": ["No geometry is sent to THAW or GTA by this probe.",
                            "Destroyed/disabled entities and dynamic transforms are not reconciled.",
                            "Successful catalogue lookup does not prove collision, grind, animation or gameplay parity."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--radius", type=float, default=128)
    parser.add_argument("--live-ipls", action="store_true",
                        help="Read current activation of the five supported conditional IPLs")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate_options(args.pid, args.seconds, args.radius)
    if not args.database.is_file():
        raise ValueError("Geometry database does not exist")
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing probe report")
    observer, ipls = Observer(args.pid), None
    try:
        if args.live_ipls:
            from inspect_ipls import IplObserver
            ipls = IplObserver(observer, args.database)
        report = run_probe(observer, args.database, args.seconds, args.radius, ipls=ipls)
    finally:
        if ipls is not None:
            ipls.close()
        observer.close()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), **report["summary"]}, allow_nan=False))


if __name__ == "__main__":
    main()
