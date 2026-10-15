"""Read-only observer for the native GTA adapter's versioned state mapping."""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import math
import ntpath
from pathlib import Path
import struct
import time

EXE_SHA256 = "a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26"
BODY = struct.Struct("<8s4I3Q8I9fI128s6I")
MAPPING_SIZE = BODY.size + 8
assert BODY.size == 272 and MAPPING_SIZE == 280


class SnapshotBusy(ValueError):
    """The native publisher did not finish a coherent mapping copy in time."""


def read_mapping_body(view, size, api, process, label, *, deadline=None):
    if not view or not process:
        raise ValueError(f'{label} reader is closed')
    now = time.monotonic()
    until = now + .02 if deadline is None else min(now + .02, deadline)
    while now < until:
        exit_code = W.DWORD()
        if not api.GetExitCodeProcess(process, C.byref(exit_code)) or exit_code.value != 259:
            raise ValueError('GTA process ended')
        begin = C.c_uint32.from_address(view).value
        if begin and not begin & 1:
            body = C.string_at(view + 4, size)
            end = C.c_uint32.from_address(view + 4 + size).value
            if begin == end == C.c_uint32.from_address(view).value:
                return body
        # A preempted writer cannot finish while a reader exhausts a tight spin.
        time.sleep(min(.001, max(0., until - time.monotonic())))
        now = time.monotonic()
    raise SnapshotBusy(f'No complete {label} available')


def decode(body: bytes, pid: int, created: int, now_ms: int,
           expected_profile: str | None = None) -> dict:
    if len(body) != BODY.size:
        raise ValueError("Invalid GTA snapshot length")
    fields = BODY.unpack(body)
    magic, version, size, owner, capabilities, creation, sequence, tick = fields[:8]
    if not pid or not created or (magic, version, size, owner, creation) != (b"GTATHAW2", 2, BODY.size, pid, created):
        raise ValueError("State belongs to a different protocol or process lifetime")
    if not sequence or tick > now_ms or now_ms - tick > 1000:
        raise ValueError("State is not fresh; gameplay may be paused or still loading")
    if capabilities & 3 != 3 or fields[25] != 0:
        raise ValueError("Profile isolation/telemetry prerequisites missing")
    profile = fields[26]
    if b"\0" not in profile or not profile[0]:
        raise ValueError("Invalid isolated profile path")
    try:
        profile_path = profile.split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Isolated profile path must be ASCII") from error
    drive, tail = ntpath.splitdrive(profile_path)
    if (len(drive) != 2 or drive[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            or drive[1] != ":" or not tail.startswith("\\") or len(tail) <= 1
            or "/" in profile_path or any(ord(char) < 32 or ord(char) > 126 for char in profile_path)
            or ntpath.normpath(profile_path) != profile_path):
        raise ValueError("Isolated profile path must be a canonical absolute drive path")
    if expected_profile is not None and ntpath.normcase(profile_path) != ntpath.normcase(expected_profile):
        raise ValueError("Profile path is outside the selected GTA installation")
    if not all(math.isfinite(value) for value in fields[16:25]):
        raise ValueError("Non-finite player state")
    if any(abs(value) > 100000 for value in fields[16:19]):
        raise ValueError("Player position exceeds supported bounds")
    if any(abs(value) > 10000 for value in fields[19:22]):
        raise ValueError("Player velocity exceeds supported bounds")
    ped, epoch, area, ped_area, timer, frame, f7, flags = fields[8:16]
    hour, minute, second, controls, cutscene, motion = fields[27:33]
    # CClock::Update rolls minutes only when elapsed > period, so equality emits 60.
    if hour > 23 or minute > 59 or second > 60:
        raise ValueError("Invalid GTA clock")
    if controls > 0xffff or cutscene > 3 or motion > 7:
        raise ValueError("Invalid GTA scene/control state")
    if flags & 1 and (not ped or not epoch):
        raise ValueError("Missing current player identity")
    if not flags & 1 and (ped or motion):
        raise ValueError("Player address is present without an active player")
    eligible = (flags & 17 == 17 and not flags & (2 | 4 | 32)
                and fields[23] > 0 and area == ped_area and controls == 0 and cutscene == 0)
    return dict(protocol="GTATHAW2", pid=pid, creation_100ns=creation,
                sequence=sequence, age_ms=now_ms-tick, capabilities=capabilities,
                ped_address=ped, epoch=epoch, area=area, ped_area=ped_area,
                game_timer=timer, frame_counter=frame, f7_requests=f7, flags=flags,
                position=list(fields[16:19]), raw_gta_velocity=list(fields[19:22]),
                heading=fields[22], health=fields[23], time_step=fields[24],
                profile_path=profile_path,
                clock_hour=hour, clock_minute=minute, clock_second=second,
                controls_disabled_mask=controls, cutscene_flags=cutscene, ped_motion_flags=motion,
                grounded=bool(motion & 1), control_eligible=bool(eligible),
                skating_available=False)


class Observer:
    def __init__(self, pid: int):
        self.pid = pid
        self.process = self.mapping = self.view = None
        self.api = C.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "OpenProcess": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            "GetProcessTimes": ([W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL),
            "QueryFullProcessImageNameW": ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
            "OpenFileMappingW": ([W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE),
            "MapViewOfFile": ([W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.c_size_t], C.c_void_p),
            "UnmapViewOfFile": ([C.c_void_p], W.BOOL),
            "CloseHandle": ([W.HANDLE], W.BOOL),
            "GetTickCount64": ([], C.c_uint64),
            "GetExitCodeProcess": ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        try:
            self.process = self.api.OpenProcess(0x1000, False, pid)
            if not self.process:
                raise C.WinError(C.get_last_error())
            path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not self.api.QueryFullProcessImageNameW(self.process, 0, path, C.byref(size)):
                raise C.WinError(C.get_last_error())
            self.exe = Path(path.value)
            if self.exe.name.lower() != "gta_sa.exe" or hashlib.sha256(self.exe.read_bytes()).hexdigest() != EXE_SHA256:
                raise ValueError("Selected process is not the verified GTA installation")
            times = [W.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(self.process, *(C.byref(item) for item in times)):
                raise C.WinError(C.get_last_error())
            self.created = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
            self.mapping = self.api.OpenFileMappingW(4, False, f"Local\\THAW_GTA_STATE_V2_{pid}")
            if not self.mapping:
                raise C.WinError(C.get_last_error())
            self.view = self.api.MapViewOfFile(self.mapping, 4, 0, 0, MAPPING_SIZE)
            if not self.view:
                raise C.WinError(C.get_last_error())
        except BaseException:
            self.close()
            raise

    def read(self, *, deadline=None) -> dict:
        body = read_mapping_body(self.view, BODY.size, self.api, self.process,
                                 'state frame', deadline=deadline)
        expected_profile = str(self.exe.parent / "thaw_gta" / "profile")
        return decode(body, self.pid, self.created, self.api.GetTickCount64(), expected_profile)

    def close(self):
        if self.view:
            self.api.UnmapViewOfFile(self.view)
            self.view = None
        for name in ("mapping", "process"):
            handle = getattr(self, name, None)
            if handle:
                self.api.CloseHandle(handle)
                setattr(self, name, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    observer = Observer(args.pid)
    try:
        until = time.monotonic() + max(0, args.seconds)
        frames = []
        while True:
            frames.append(observer.read())
            if time.monotonic() >= until:
                break
            time.sleep(0.1)
        result = dict(executable=str(observer.exe), samples=len(frames),
                      first=frames[0], last=frames[-1],
                      distinct_sequences=len({frame["sequence"] for frame in frames}))
        rendered = json.dumps(result, indent=2)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        observer.close()


if __name__ == "__main__":
    main()
