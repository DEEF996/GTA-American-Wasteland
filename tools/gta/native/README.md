# GTA native adapter: first verified interface

The current gameplay integration is documented in
[`docs/gta-first-run.md`](../../../docs/gta-first-run.md); the sections below
also retain the early observer/probe history.

The staged `GTAFX001` effect transport is a read-only 104-byte packet in the
112-byte `Local\THAW_GTA_EFFECTS_V1_<pid>` seqlock. It carries the exact process
lifetime and movement identity, real timestamp, current time/effect/SFX-volume
scalars and one-shot transition sequence/kind. The adapter alone owns the
paired GTA timer/audio-pitch scalars; compare/exchange rollback preserves foreign
changes. RMB + a fresh Shift press starts the bounded five-real-second effect.
The source host paces entire original frames and maintains fresh ordinary
movement heartbeats between them, leaving the normal weapon firing path active.
Geometry/native-operation waits remain distinct buffered heartbeats. Both
presentation clocks use the same explicit slow-motion scale and QPC time.
The build verifies these policies offline; installation/live gameplay testing
of this staged update remains pending.

This is an x86 ASI for one verified GTA San Andreas 1.0 US executable. It is
normally a **read-only gameplay observer**, with an isolated game profile.
Separate opt-in developer tests preview original poses/board and briefly hold
idle CJ movement. An optional startup setting removes GTA's legacy frame delay.
These tests do not enable skating or claim a connected vanilla THAW gameplay
runtime.

Build with `build.ps1` and existing Visual Studio C++ x86 tools. No Plugin-SDK
checkout or library is needed. The build runs the protocol assertions before
creating `thaw_gta.asi`; it never installs or launches the game.

Use Ultimate ASI Loader with its exported `InitializeASI` calling convention.
The adapter's `DllMain` does not patch the game or perform filesystem work.
The supported executable must have SHA-256
`a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26`.
The image base, process call bytes and profile getter entry are checked again
in memory. Other executable versions and preexisting conflicting hooks fail
before control is acquired.

## Profile isolation

Initialization replaces GTA's user-directory getter before its first call.
It returns `<game>/thaw_gta/profile`, with `Gallery` and `User Tracks` children,
and initializes the corresponding three original path buffers. GTA's normal
save/settings functions therefore retain their behavior in the isolated
directory. Original Documents saves and `gta_sa.set` are not imported or edited.
The game has a 128-byte directory buffer; currently the isolated path must be
ASCII and leave room for the longest child path. Symlink/junction profile
directories are refused. A loader that runs too late is refused.

Initialization failures terminate this launched process with exit code 86;
continuing with a failed redirect could write the normal game profile. The
reason is written to `<game>/thaw_gta/adapter-status.json` when that directory is
writable. A successful initialization reports `profile_active: true`. This
means the redirect was installed; live verification must additionally observe
`profile_calls > 0` and check where settings were actually written.

## State transport

The adapter wraps GTA's normal `CGame::Process` call and runs the observer after
the original call returns. The observer executes on the game thread, without
a polling thread touching game structures. It exposes the current player,
position, raw GTA velocity, heading, health, current interior, ped interior,
timer, pause state, frame count and F7 request edges. V2 also reads the actual
GTA clock, cutscene running/processing flags, the focused player's disabled
control mask, and the ped's standing/in-air/landing bits in that same capture.
An epoch changes when the player, interior, game timeline, disabled control mask
or cutscene flags change. Ground/air transitions do not change the epoch.
Coordinates and velocities are
not relabeled as THAW units; the host must perform an explicitly tested unit
conversion. F7 only records a request and does not suppress ordinary GTA input.

The `Local\THAW_GTA_STATE_V2_<pid>` mapping is exactly 280 bytes. See
`bridge_protocol.h`: a 4-byte generation precedes the 272-byte snapshot, and a
4-byte ending generation follows it. A reader accepts only a nonzero even
starting generation equal to both the ending and reread starting generations.
It must also check PID, process creation time, protocol size and freshness.
`inspect_state.py --pid <pid> --seconds 3 --output <report.json>` is a read-only
standard-library reader. It verifies the actual process image hash and lifetime.
No game control, focus changes or inputs are performed by this inspector.

The magic is `GTATHAW2`, version 2. Original offsets through the 128-byte profile
path at offset 120 remain unchanged. The following six `uint32` fields are
appended at body offsets 248, 252, 256, 260, 264 and 268:

- `clock_hour`, `clock_minute`, `clock_second`: raw fields, bounded by 23/59/60.
- `controls_disabled_mask`: the unmodified 16-bit CPad mask.
- `cutscene_flags`: running = 1, processing = 2.
- `ped_motion_flags`: standing = 1, in air = 2, landing = 4.

The new mapping name prevents an older reader from attaching to a V2 transport.
Malformed clock/mask values are rejected. Both readers additionally expose
`grounded` from the standing bit and `control_eligible`: a living on-foot ped in
the current area, with no pause, read fault, vehicle, cutscene or disabled
controls. Being airborne does not make a ped ineligible. This is an observed
gate, not a movement takeover implementation; it does not advertise any extra
runtime capability. Conditional IPL activation and dynamic transforms remain
unavailable in this snapshot.

Second 60 is an original GTA boundary value, not a normalized wall-clock second.
[`CClock::Update`](https://github.com/gta-reversed/gta-reversed/blob/master/source/game_sa/Clock.cpp)
increments minutes only when elapsed time is strictly greater than its period;
at equality its final seconds calculation yields 60. The pinned local EXE has
the corresponding `JG` at `0x52CF31`. Read-only live evidence in
`research/gta-clock-boundary-02.json` observed three such frames among 3600:
snapshot/native fields agreed, and timer minus last tick equalled period=1000.
The readers preserve this value and still reject 61, invalid hours/minutes and
all other malformed state. No game clock is written or normalized. Timed IDE
queries use raw hours, matching original `CClock::GetIsTimeInRange`.

The current adapter advertises only telemetry and isolated-profile capabilities.
Before a future movement owner can enter THAW mode, the host must have the real
vanilla THAW runtime, current scene collision/grind data, ped presentation and
camera ready for this process lifetime, player epoch and area. The prerequisite
predicate also requires `control_eligible`. It is tested; the developer hold
receiver described below is separate from a gameplay movement owner.
World collision must be refreshed as GTA streams areas and conditional IPLs;
an offline whole-map index alone does not prove the active scene is current.

Existing `THAWVIS2` skeleton/board poses and `THAWCAM1` solved camera data remain
reusable host formats. Their old WaW PID labels, units, skeletal mapping and
render addresses are not a GTA implementation. The bounded GTA RenderWare
preview below has a separate transport; movement unit conversion still needs
its own verification.

## Optional legacy frame delay removal

Create `<game>/thaw_gta/adapter.ini` with:

```ini
[performance]
RemoveLegacyFrameDelay=1
```

The default is `0` (disabled). When enabled at startup, the adapter checks the
exact five bytes `83 F8 0E 72 E1` at `0x53E94A`, then changes only the immediate
at `0x53E94C` from 14 to 0. The already-fixed bytes `83 F8 00 72 E1` are accepted;
any other sequence refuses initialization. Memory protection is restored and
the instruction cache is flushed. No executable file is modified.

The original loop waits at least 14 milliseconds per frame, preventing 120 FPS
even when another mod raises the game's limiter. This patch follows the
[FramerateVigilante implementation](https://github.com/GTAmodding/FramerateVigilante/blob/master/FramerateVigilante/FramerateVigilante.cpp).
It does not fix frame-dependent physics, select a refresh rate, or impose an
FPS cap. A separately verified physics fix and limiter are still required.
`adapter-status.json` reports `legacy_frame_delay_removed: true` only when this
option was enabled and the patch was applied or its exact fixed bytes verified.

This small patch avoids adding all of SilentPatch solely to remove the delay:
[SilentPatchSA's source](https://github.com/CookiePLMonster/SilentPatch/blob/1.1-BUILD34.1-SA/SilentPatchSA/SilentPatchSA.cpp)
also replaces the user-directory getter at `0x744FB0`, which conflicts with this
adapter's profile isolation. Neither the optional delay patch nor the configured
limiter guarantees 120 FPS in every scene; measure the running game.

## Address provenance

The local executable was inspected read-only. Version detection and ABI offsets
were checked against these primary Plugin-SDK sources on 2026-10-11:

- [GameVersion.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/shared/GameVersion.cpp):
  HoodLum 1.0 US signature at `0x401000`.
- [Events.h](https://github.com/DK22Pac/plugin-sdk/blob/master/shared/Events.h) and
  [CGame.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CGame.cpp):
  process call at `0x53E981`, original function `0x53BEE0`, current area `0xB72914`.
- [common.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/common.cpp):
  `FindPlayerPed(-1)` at `0x56E210`.
- [CPlaceable.h](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CPlaceable.h),
  [CPhysical.h](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CPhysical.h),
  [CPed.h](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CPed.h),
  [CEntity.h](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CEntity.h),
  [CTimer.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CTimer.cpp):
  player and timer layout.
- [CFileMgr.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CFileMgr.cpp):
  `SetDirMyDocuments` at `0x538860`. Its getter target `0x744FB0` and the profile,
  Gallery and User Tracks buffers were verified in the local image.
- [CClock.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CClock.cpp),
  [CCutsceneMgr.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CCutsceneMgr.cpp),
  [CPad.h](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CPad.h),
  [CPad.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CPad.cpp) and
  [CWorld.cpp](https://github.com/DK22Pac/plugin-sdk/blob/master/plugin_sa/game_sa/CWorld.cpp):
  clock at `0xB70150/52/53`, cutscene flags at `0xB5F851/52`, focus at
  `0xB7CD74`, CPad array at `0xB73458` with stride `0x134` and controls offset
  `0x10E`. CPed flags begin at `0x46C`; standing is bit 0, in-air/landing bits
  9/10. Original operand instructions for these layouts are checked at startup
  alongside the pinned executable hash. These are memory reads only.
- [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader):
  loader installation and early plugin initialization.

The ASI contains our adapter code and short compatibility guards. It does not
contain the GTA executable, assets, or a copy of Plugin-SDK implementation files.
## Opt-in, bounded CJ pose preview

The adapter can temporarily draw one audited THAW pose on the observed CJ
skin. This is a developer presentation test, not skating or movement ownership.
It is off by default. In the isolated game's `thaw_gta/adapter.ini`, enable it
before restarting the game:

```ini
[development]
PosePreview=1
```

The guarded CPlayerPed vtable Render slot at `0x86D1B0` calls a wrapper around
original `CPed::Render` (`0x5E7680`). Only a fresh `GTAPOSE5` packet for the current
player, process lifetime, epoch, area, clump, skin, node table and inverse binds
can pass. Its maximum age is 100 ms. The wrapper backs up all 32 RenderWare
matrices, writes the numeric pose for this draw, calls the original renderer,
and restores **every original byte** in `__finally`. It applies native
`RwMatrixUpdate` (`0x7F18A0`) to staged matrices; RW flags/padding are not confused
with affine floats. The packet's model-space root is anchored to the current
native root, so an old world-space report cannot teleport the presentation.

After each restart, player load or clothing/rig change, first produce a fresh
rig report with `tools/gta/inspect_rig.py`, then regenerate the offline pose
with `tools/gta/retarget_pose.py`. Do not substitute new addresses into an old
report. The publisher checks the target-rig SHA link, all three offline source
hashes, live pointers/nodes/binds and current scene before sending at 30 Hz:

```powershell
python -B tools/gta/preview_pose.py --pid <current-GTA-pid> --target-rig research/gta-cj-rig-fresh.json --pose-report research/gta-retarget-fresh.json --frame 1 --seconds 5
```

Duration defaults to 5 seconds and is limited to 10. Any scene, focus, control,
rig or freshness rejection stops publication and clears the packet. If the
publisher crashes, the packet expires after 100 ms; each completed draw has
already restored native bones. A named mutex prevents concurrent publishers.
The process handle is read-only; commands use only the ASI-owned pose mapping.
The Python probe uses aligned 32-bit seqlock stores and exported
[`FlushProcessWriteBuffers`](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-flushprocesswritebuffers)
barriers because 64-bit Kernel32 does not export the interlocked intrinsic.
These barriers are suitable for the short developer test; the eventual
continuous native host should use its own interlocked publication.

Read `thaw_gta/adapter-status.json` after the test: `pose_preview_enabled`,
`pose_preview_applied`, `pose_preview_restored`, `pose_preview_rejected`, and
`pose_preview_faults`. Successful completed draws increase applied/restored
equally, with faults zero. Rejected includes ordinary draws without a packet;
it is not itself a fault count. Status refreshes once per second. A native
validation exception latches preview off for the session while ordinary render
continues. None of these counters enables the gameplay capabilities or F7.

`build.ps1` runs synthetic packet/root-anchor tests and full-byte restoration
tests for both a normal render and a deliberately raised structured exception.
`python -B -m unittest discover -s tools/gta -p test_preview_pose.py -v` tests
publisher boundaries without private assets or a running GTA process.

An optional original-board bundle can accompany the same sample; see the
[private preparation tool](../board_preview.py), [native adapter](board_preview.cpp)
and [restoration tests](board_preview_test.cpp). The current `GTAPOSE5` includes
the immutable bundle SHA, four original board palettes and the accepted movement
request/source tick/source microsecond clock, a rigid model display transform and
the original board visibility and detached-board world presentation (5168-byte
body, 5176-byte mapping). Detached boards retain native object identity, cloned
palette and final orientation; their world translation uses physical units,
independent of the scale used to fit the board to CJ. Transitions between two
different board owners never interpolate unrelated palettes.
Old V1/V2/V3/V4 publishers cannot attach to its differently named mapping. `--motion`
replays the audited dense original Ollie samples without interpolation;
mesh/texture resources remain unchanged while CPU skinning updates positions.

Continuous gameplay interpolates local bone rotations, translations, board
palettes and the matching actor root between original simulation samples. This
is a render-only delay of at most one source interval, with no extrapolation or
physics-rate change. Heartbeats refresh freshness without advancing that sample
clock. `pose_interpolated` counts draws between samples. Static previews use a
zero movement request and keep their original unblended behavior.

The stock model's completed geometry-instance transform supplies aerial flips,
rolls and slope lean. Python removes only world position and yaw already owned
by MOVE2, then converts the remaining rotation/pivot using the same pelvis
calibration as the board. V5 keeps the structural root and bone-length checks
unchanged; native rendering applies the separately bounded display transform to
all 32 bones, including a potentially weighted root. The four board palettes
receive the equivalent source-space transform. This does not change physics,
retime original animations or apply source model scale twice.

Limitations: one single-atomic 32-bone CJ rig, one original test clip and no
movement control. Shadows and attachments prepared before Render are outside
this first test, and four finger mappings remain disabled. The exact original
Render path does not rebuild the HAnim allocation during the scoped call; a
different renderer, ASI hook or rig requires a new audit. Disable `PosePreview`
and restart to remove the optional vtable hook.

## Opt-in, bounded idle-CJ hold

This independent developer proof passed a one-second live hold (120 ticks),
stale-heartbeat recovery before explicit publisher clear, and ordinary walking
after release. Flag sets/restores matched, with zero faults and unchanged zero
release velocity. Moving and airborne handoff remain unverified. It adds no
F7 behavior and activates no rendering. See the
[current integration guide](../../../docs/gta-first-run.md) for gameplay setup and validation status.
In the isolated clone only, opt in before restart:

```ini
[development]
MovementHoldPreview=1
```

The default is `0`; existing `PosePreview` settings are independent. Exact
original code hashes, the player vtable and three callsites are checked before
installing the optional wrappers. A changed/conflicting hook refuses startup.
The proof keeps GTA's original controller/events/tasks and world-sector update
paths. Only its owned CJ can skip gravity/velocity/position integration, with
a scoped native collision branch that avoids a second integration. The single
collision flag is restored after each game tick, including structured-exception
unwinding while the actor still matches. Original positions, velocities and
input bytes are never overwritten by this proof.

Use a loaded, foreground, grounded, idle CJ with no game input:

```powershell
python -B tools/gta/preview_hold.py --pid <current-GTA-pid> --seconds 1
```

Duration defaults to one second and is limited to two. `GTAHOLD1` is a 72-byte
body in an 80-byte `Local\\THAW_GTA_HOLD_V1_<pid>` mapping. The command carries
only session/scene identity, monotonic request ID, sequence, heartbeat and
duration. It has no coordinates or velocity command. The adapter captures
current transform and kinematics itself, requires heartbeats no older than
100 ms and never extends the initial deadline. A request can be consumed once.
A partially published seqlock uses only the last validated heartbeat within
that same 100 ms bound. An explicitly cleared mapping or fully published bad
packet ends immediately; neither uses the previous packet as a fallback.
Validation samples the clock after copying the packet, avoiding a false future
timestamp when publication crosses a millisecond boundary.
The read-only observer verifies the EXE hash, process lifetime and profile;
the command publisher reuses the existing seqlock and exclusive publisher
mutex. Its final JSON proves publication only; inspect native counters too.

Only the ordinary idle on-foot task is accepted. Input, competing task, damage,
cutscene, foreground/scene change, replaced actor, changed transform/heading
or velocity cancels. A native task/event's new velocity is preserved, including
when the transform stayed unchanged. No motion value is reset on timeout.

After publication ends, allow the usual one-second status refresh and read
`<game>/thaw_gta/adapter-status.json`. A successful bounded test should increment
`hold_accepted` and `hold_completed`, leave `hold_active: false`, show controller
and three skip counters increasing, match `hold_flag_sets` with
`hold_flag_restores`, and keep `hold_faults: 0`. For an undisturbed hold,
`hold_release_observed` must be true and `hold_entry_velocity` and
`hold_release_velocity` must match. A replaced actor is not read for release;
its previous release numbers are invalid when `hold_release_observed` is false.
Status includes
the request/start time and entry position for correlation with read-only scene
samples; zero publications accepted is not a successful movement test.

`hold_last_reason`: 0 active/none, 1 deadline, 2 cleared/stale/invalid packet,
3 changed/invalid actor identity, 4 eligibility, 5 foreign root/heading write,
6 memory/numeric fault, 7 missing original controller callback, 8 foreign
linear/angular velocity write. For reason 4, `hold_last_gate` is 1 scene/basic
actor, 2 competing primary task, 3 missing/changed default on-foot task,
4 unsafe secondary task, 5 controller input. A consumed rejected request will
not repeatedly retry; a fresh invocation is required. Faults latch new holds
off until restart.
`hold_busy_reads` counts encountered in-progress publications;
`hold_last_packet_state` is 1 cleared, 2 in progress or 3 committed (0 before
any accepted request). These distinguish transport contention from actual
heartbeat expiry without changing any lease or scene condition.

`build.ps1` additionally runs the synthetic hold packet/deadline/replay and
normal/SEH collision-bit restore tests. Pure Python tests:

```powershell
python -B -m unittest discover -s tools/gta -p test_preview_hold.py -v
python -B -m unittest discover -s tools/gta -p test_preview_pose.py -v
```

The initial scope deliberately excludes moving/airborne handoffs and does not
prove native task/damage semantics under every interaction. Disable
`MovementHoldPreview` and restart to remove its callsite/vtable wrappers.

## Opt-in commanded planar movement proof

The first live test passed: 60 commands, 120 controlled ticks, about 0.246 m
of horizontal movement and 14.75 degrees of yaw, with matched collision-flag
restoration and zero faults. The last nonzero command velocity survived at
release. Normal GTA idle processing subsequently reduced velocity to zero.
This proves the bounded planar command path, not skating or midair handoff.

`MovementCommandPreview=1` in the same `[development]` section enables a
separate `GTAMOVE1` command mapping. Its default is `0`. The existing `GTAHOLD1`
layout, mapping and stationary behavior remain unchanged; either feature uses
the same guarded controller hooks, and only one request can own CJ at a time.
If both new requests arrive together, both are rejected. Turn both settings
off and restart to remove the shared hooks.

```powershell
python -B tools/gta/preview_move.py --pid <current-GTA-pid> --seconds 1 --speed 0.25 --yaw-degrees 15
```

This requires an untouched controller and a stationary, upright, grounded CJ
on a clear, level patch. It inherits all hold identity, task, health and input
gates. It does not test flight or a THAW-driven skater. The command body is
104 bytes in `Local\\THAW_GTA_MOVE_V1_<pid>` (112 bytes including seqlock).
After the original 72-byte lease fields come XYZ world metres, yaw radians,
raw GTA XYZ velocity, and a zero reserved word. No THAW-unit values belong in
this transport: physical velocity in metres/second is divided by 50 exactly
once, with no additional timestep multiplier.

The lease lasts at most one second and cannot be renewed. Heartbeat expiry is
100 ms. The receiver permits a one-metre radius, unchanged Z, zero vertical
velocity, at most 1 m/s of declared speed and 90 degrees from the entry yaw.
Each position/yaw step is limited by packet elapsed time with a small sampling
tolerance; this remains a bounded developer command, not an integrator.
The first command must be within 2 cm and 0.02 rad of the actual captured
entry. A repeated sequence must contain identical bytes, including timestamp.

On a new command, the game thread changes the existing CMatrix through native
SetHeading, writes position/current and desired yaw/raw velocity, calls native
RemoveAndAdd, copies that matrix to the existing RenderWare frame and updates
the frame. All these entries are pinned before hooks are installed. The
original controller/events/tasks continue. Unexpected task/root/velocity
changes end ownership and survive. There is no repeated Teleport or task flush.
On clear, expiry or cancellation, GTA resumes from the latest applied state;
the proof never restores the entry position or zeroes release velocity.

`move_enabled`, `move_last_request_mode`, `move_commits`,
`move_applied_sequence`, `move_last_position` and `move_last_velocity` supplement
the existing `hold_*` status. Those common counters now cover both proof modes;
`hold_active` plus `move_last_request_mode` identifies an active movement
request. `hold_last_reason: 9` means a command bound or competing request
rejection. Positive publications alone do not prove movement: check native
commits, independently observed XYZ/yaw, release velocity and matching flag
sets/restores. The moving stale-heartbeat test also passed before explicit
mapping clear, followed by ordinary W movement with control eligibility intact.

Five new Python tests and the twelve existing hold/publisher tests passed.
The command lease/numeric/replay checks were also added to
`movement_hold_test.cpp`, already included in `build.ps1`. Root compiled and
installed the guarded extension in the clone before the live result above;
see the [current integration guide](../../../docs/gta-first-run.md) for
gameplay setup, original-code boundaries and validation status.

The separate default-off `MovementTrajectoryPreview=1` option enables MOVE3
(`GTAMOVE3`, body144/shared152). It accepts bounded original 3D samples for at
most five seconds, within 64 metres and 20 m/s, preserving the actual raw
velocity instead of rescaling it. Source tick/time must progress, collision
generation stays fixed, and both heartbeat and stopped-source expiry are 100 ms.
Ground/air contact is explicit; task or unexpected movement/contact changes
still release control. Existing HOLD1/MOVE1 modes retain their original guards.
The Python publisher requires an actual stationary native entry frame:

```powershell
python -B tools/gta/preview_gameplay_move.py --pid PID --capture CURRENT-CAPTURE.frames.json --start-frame 16 --collision-generation 1
```

This is a development trajectory replay, not F7 gameplay or live collision
streaming. The original recorded speed is unchanged, the path uses a fixed
translation to CJ, and the visual yaw uses a relative entry offset. See the
[MOVE3 protocol](movement_trajectory_protocol.h) and [publisher](../preview_gameplay_move.py)
for wire offsets, contact handling and diagnostics.
The current packet appends original mode, skating state, walking state and
independent bail status. They share the committed source tick/time; duplicate
ticks and buffering cannot replace the context. Preparation/tick0 has no
completed source context. Old MOVE2 publishers and captures without bail data
must be updated instead of guessing the missing state.

The continuous MOVE3 path also recognizes contact flag `0x400` for a witnessed
original ceiling correction. The producer verifies an unchanged stock actor,
a matching original head-clearance query and either Air-to-Air motion or the
specific Wallride-to-Air 18/36-inch clearance. The native receiver retains the
ordinary horizontal bound for Air and adds at most 36 inches only after an
acknowledged non-bailing skating Wallride (state 2). Both cases require a fresh
Air sample, zero vertical velocity and a downward correction of at most 2 m.
Other wall/hang transitions and ordinary samples retain their existing limits.
The publisher retains the flagged sample through buffering and waits for its
acknowledgement before stepping the original simulation again.

An additional default-off `MovementInteractivePreview=1` enables scoped pad0
input ownership and MOVE2 leases up to 30 seconds/256 metres. It requires the
trajectory option above; callers use `packet(..., interactive=True)`. The full
native CJ controller still runs with temporarily neutral NewState/OldState,
restored in an exception-safe scope. Unexpected pad writers are preserved and
abort ownership. Raw OS keyboard/mouse input remains available to the host.
F7 request changes also abort the native lease independently of the publisher.

The unchanged GTATHAW2 status word now exposes actual `TrajectoryOwned=64` and
game-thread `NumLockOn=128`; existing readers can inspect `flags`. Input scopes
and restore conflicts are counted in `trajectory_pad_*`. Reason11 is F7 stop,
reason12 is a pad conflict, and eligibility gate6 rejects a different pad/focus.
These options remain development proofs pending their separate live tests;
they do not enable a general gameplay-ready capability.

The continuous MOVE2 path (duration zero) also has a narrowly scoped rail-capture
allowance as of 2026-10-13: a fresh non-rail to rail transition may add up to 2 m
to the usual displacement bound. Both source clocks must advance. First samples,
buffered samples, repeated rail motion, ordinary ground/air movement and bounded
previews do not receive the allowance. This is an adapter limit; it is not a
recovered THAW constant and does not modify original THAW simulation.
Velocity ownership uses exact finite numeric equality, so changing the sign of
zero alone no longer releases the lease. Genuine velocity changes remain guarded.

`hold_rejection` records the failed and previously applied samples before cleanup,
with its own request/reason and expected/observed linear and angular velocity.
Historical diagnostics must be matched to that request. The host release event
also keeps its last four source commands. These fields address the old logs'
missing exact rejection sample. Native standalone checks pass; actual grind
continuity and rendering smoothness still require the user's gameplay test.

