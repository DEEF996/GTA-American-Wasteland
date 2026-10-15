# GTA THAW — local setup and integration source

This project connects original PC Tony Hawk's American Wasteland gameplay to
an isolated copy of GTA San Andreas. GTA presents CJ, the board, camera and HUD.
Our hidden `gta-thaw-runtime.exe`, written in Rust, loads the supported local
THAW executable's original code and data and calls its gameplay functions. It
does not launch `THAW.exe` or enter the original game's main loop. The original
physics, tricks and animation logic still execute; this is not an assets-only mod.
It is a development build. It is not a complete independent Rust rewrite, and
complete compatibility with every GTA modification is not claimed.

<a id="verification-status-2026-10-14"></a>

## Verification status (2026-10-15)

This is the current development status. A feature being implemented does not
mean that every trick, map location or interaction has been tested.

### Current performance work (2026-10-15)

**The final normal-mode run, `normal09`, retained THAW through 40 scripted cycles
and ended with the deliberate final J press.** Its controller took 220.94 seconds;
the active session lasted 220.70 seconds. Frame profiling and Ground tracing were
disabled. Eleven position corrections were accepted, ranging from **1.023 to
1.434 m**: four Rail samples including a Rail bail, five Wallride, one Air and
one Ground bail. The earlier `source06` also accepted Ground-bail corrections
of 2.593 and 1.875 m. These events establish acceptance during bail, without
identifying first-bail onset from the current state alone.

The `normal09` approach timed out after 75 seconds, still **287.75 m** from its
target; this does not establish coverage of the intended location. Later control
samples include 28 original Rail observations and 84 Ground-bail observations.
They are cached phase-end samples, not a whole-run count of successful grinds.

Production movement now uses one bounded allowance for an owned, completed
original-source frame instead of separate rail, ceiling and body witnesses.
It preserves the original position and velocity. The additional **2 m** is a
transport tolerance above the ordinary `60 m/s × elapsed source time + 0.02 m`
distance bound, approximately **3.02 m total** for one 60 Hz frame; it is not a
claimed maximum of the original collision solver. It requires consecutive source
frames, a closed original 60 Hz clock and the exact acknowledged predecessor.
The first completed sample establishes the baseline. Entry, finite-value,
velocity, source ownership and collision-coverage guards remain in force; the
flagged sample must also be acknowledged before another source step, including
after closing F8. Earlier per-cause probes remain diagnostic evidence, not the
production acceptance mechanism. Offline fixtures cover the four captured
1.077–2.172 m bail corrections. The live Ground-bail events above are separate
cases, not exact replays of those captured frames.

The latest completed full Python suites passed **602 GTA tests and 469 original-engine
integration tests**. The transport integration also passed 64 focused checks;
the installed ASI passed 17 native test executables, and the Rust host previously
passed 9 tests. These checks and the bounded live results below do not establish
uninterrupted animation in every situation.

Measured costs now have narrower fixes:

- The native command cache retains 64 pages instead of 32. Local counters in
  `perfcounter01` measured **82.01% cache misses** with 32 pages. The completed
  `perfcombined01` and `combinedgrind01` runs measured **0.261% and 0.895%** with
  64 pages. These are observed cache ratios; different routes and traffic prevent
  treating the change in total frame time as a matched speed comparison.
- Rail resources now enter the original memory-loading path with its original
  copy, asset ownership and unload behavior. Twenty alternating load/parse/unload
  cycles used the exact 806-node resource responsible for the earlier 90 ms
  stalls: disk median **88.68 ms**, memory median **0.943 ms**, memory maximum
  **1.313 ms**. Live memory-load maxima were **0.584 ms** in `perfcombined01`
  and **0.419 ms** in `combinedgrind01`; those live runs used other resources.
- Moving-vehicle collision meshes use the original bounded BSP traversal.
  One real 28,496-triangle shape went from 1,024 child meshes to 28, retaining
  ordered float32 triangle geometry, winding, flags and materials. There were
  no hit/normal/position mismatches within the stated tolerances across 788
  original queries per variant, including translated and rotated owners.
  Static-world geometry and sphere accuracy are unchanged. This is collision
  workload evidence, not a measured universal GTA FPS gain.
- Skeleton matrix multiplication removes repeated generator overhead while
  preserving `sum` and the order of its four terms. Eight captured real palettes
  produced bit-identical output. Offline retargeting fell from **1.526 to
  0.925 ms per pose**, about **39%**; 23 focused checks and independent review
  passed. This measures retargeting CPU time, not whole-game FPS. The earlier
  combined01–03 runs predate this optimization; `groundtrace02` includes it,
  but uses a different route and additional diagnostic instrumentation.
- Each changed vehicle's original transform operations now share one worker
  command. An isolated seven-vehicle comparison measured median update cost
  **1.89–1.91 ms before and 1.36 ms after**, about **28% lower**, with 14 owner
  snapshots and 756 collision queries bit-identical between variants. This
  original-engine proof did not access live GTA and does not establish a
  whole-game speed improvement.
- Native-wait callbacks skip duplicate movement/pose publication when the last
  publication is less than 20 ms old. They retain the existing accepted-sample
  checks when renewal becomes due. The regression test checks fresh skips,
  renewals at 20 and 40 ms, and no extra source step. These two latest changes
  postdate `source05` and are included in `source06`; the different trajectories
  and traffic do not provide a matched live speed comparison.
- Immutable inverse-bind validation is reused only when all 2,048 freshly read
  bytes and their expected canonical binding match. All 21 pointer, node, bind
  and final-clump reads remain; changed bytes take the original validation path.
  An offline captured-CJ replay measured about **0.314 to 0.005 ms** CPU time,
  with identical packets. This excludes process-memory read time and live FPS.
- Compiler workers omit candidate provenance only from their validated runtime
  payload, after saving the complete cache data. An 852-node fixture retained
  identical native collision/rail inputs while container count fell from 6,156
  to 1,764 and median deserialization from **1.10 to 0.31 ms**. In that isolated
  32-load test, collection count fell from 32 to four without changing GC policy.
  These two changes are included in `source08`; they do not eliminate all stalls.

| Test | Result and coverage | Source cadence | Work per iteration, mean / maximum |
| --- | --- | --- | --- |
| `perfcombined01` | Completed 93.21 active seconds; 0 waypoints reached, 1 timeout, eventually blocked by a wall. | 5,585 frames; 59.92 Hz | 12.98 / 39.91 ms |
| `combinedgrind01` | Completed 46.29 active seconds; original grind contact at tick 273, then deliberate J off. | 2,768 frames; 59.80 Hz | 13.79 / 32.53 ms |
| `perfcombined02` | **Incomplete**: the harness detected an unsent Space at 116.69 seconds; 1 waypoint reached and 1 timed out. | 59.77 Hz between retained control samples | 14.24 / 56.34 ms in the controlled prefix |
| `perfcombined03` | **Failed** after 112.62 active seconds: Ground-to-Ground rejection reason 9 released THAW. | 6,724 generated frames; 59.71 Hz, final frame rejected | 13.69 / 35.91 ms |
| `groundtrace02` | Completed 301.93 active seconds / 45 scripted cycles with tracing enabled, then deliberate J off. The previous Ground failure was not reproduced. | 18,032 generated frames; 59.72 Hz; last accepted tick 18,031 | 12.89 / 50.50 ms |
| `source05` | Completed 209.29 active seconds / 40 scripted cycles, including eight accepted bounded corrections, then deliberate J off. | 12,492 generated frames; 59.69 Hz; terminal applied tick 12,491 | 13.22 / 86.34 ms |
| `source06` | Completed 220.51 active seconds / 40 scripted cycles, including nine accepted corrections and two Ground-bail cases, then deliberate J off. Approach timed out 8.65 m from target. | 13,199 generated frames; 59.86 Hz; 13,198 presented source samples | 12.72 / 75.83 ms |
| `source08` | Completed 220.76 active seconds / 40 scripted cycles, including Ground- and Rail-bail corrections, then deliberate J off. Approach timed out 287.89 m from target. | 13,215 generated frames; 59.86 Hz; 13,214 presented source samples | 12.64 / 48.97 ms |
| `normal09` | Completed 220.70 active seconds / 40 scripted cycles with profiling and Ground tracing off; eleven corrections accepted, then deliberate J off. Approach timed out 287.75 m from target. | 13,208 generated frames; 59.85 Hz; 13,207 presented source samples | Not profiled |

In `normal09`, same-process initial and final adapter snapshots bracketed J
counter 0 to 2. **All native fault and movement-rejection deltas were zero**,
with one accepted and one completed movement session. Presentation-history
corrections and their maximum magnitude remained zero; 26,418 of 26,473 rendered
pose updates were interpolated. One camera conflict occurred with one normal
reset recovered. The active-pose rejection counter increased by five without
a pose fault. The source-gap high-water mark rose from 0 to **47 ms**.
Thirteen geometry waits totalled **163.74 ms**, maximum **19.89 ms**: eleven
prepared rails and two prepared the current region. No unexpected native release
or source error was recorded before the final J press.

The completed prefix contains no frame/stage profiler, GC, serialization,
native-call timing or large-displacement trace events. Normal source-profile
loading and suppressed-log counters still appear; those are not the optional
profilers. Work-per-frame maxima are consequently **unmeasured**, not zero.
The observed 47 ms source gap also prevents a claim of hitch-free playback.
Later menu checks and any subsequent user play are excluded from these results.

In `source08`, initial and final adapter snapshots came from the same GTA process
and bracketed J counter 0 to 2. **All native fault and movement-rejection deltas
were zero**, with one accepted and one completed movement session. Presentation
history corrections and their maximum magnitude remained zero; 26,411 of 26,478
rendered pose updates were interpolated. One camera conflict occurred with one
normal reset recovered. The active-pose rejection counter increased by five,
without a pose fault. The source-gap high-water mark rose from 0 to **63 ms**.
Fifteen geometry waits totalled **200.99 ms**, maximum **21.15 ms**: 13 prepared
rails and two prepared the current region. No timed native resource call exceeded
20 ms, but work still peaked at **48.97 ms**. These measurements include optional
profiling and do not establish constant 60 Hz or removal of visible hitches.

In `source06`, 19 geometry waits totalled **195.83 ms**, maximum **17.70 ms**.
Eighteen prepared rails and one prepared the current collision region. The final
control snapshot confirms J off, one completed movement session and no movement
rejection or native fault counts. Presentation-history corrections and their
maximum magnitude were zero. Camera conflicts were one, and the source-gap
high-water mark was 94 ms. No initial adapter snapshot was retained, so these
are cumulative end values rather than measured counter deltas. No unexpected
native release was logged in the selected session; later menu checks are excluded.
The **75.83 ms** work spike remains a limitation despite the near-60 Hz average.
Mean vehicle-update time was 1.64 ms versus 1.94 ms in `source05`, but different
traffic and paths prevent attributing that difference solely to the new batch.

In `source05`, 17 rail-preparation waits totalled **154.88 ms**, maximum
**16.89 ms**. Terminal native fault, movement-rejection and presentation-history
correction counters were zero, including the maximum history-correction
magnitude. There was no initial adapter snapshot, so these are cumulative end
values, not independently measured deltas. The terminal source-gap maximum was
78 ms and camera-conflict count was one, with one normal reset recovered.
The **86.34 ms** work spike means occasional visible pauses remain possible.
The source cadence is not a matched speed comparison with earlier routes.

In `groundtrace02`, all native fault and movement-rejection counter deltas were
zero. One camera conflict was recorded; presentation-history corrections remained
zero, including their maximum magnitude. The source-gap high-water mark rose
from 0 to 62 ms. Thirty-two geometry waits totalled **339.15 ms**, maximum
**34.82 ms**; the complete iteration peaked at **52.64 ms**. The 64-page cache
miss rate was **0.928%**. Across 135 original memory loads during the active
session, the median was **0.244 ms** and maximum **0.523 ms**; the complete
loading bridge peaked at 4.46 ms. These measurements still include occasional
pauses and diagnostic overhead.

That run committed 58 scene changes covering 42 distinct collision resources.
Two retained source captures explicitly contain original rail state, at ticks
4,266 and 6,606; they do not provide a whole-run grind-frame count. One optional
empty forecast was rejected while the current scene remained valid. Three
recorded hourly boundaries retained identical collision data and did not repeat
the source frame. This is bounded traversal evidence, not coverage of every map
surface or every possible transition.

The first completed run had no new native faults, movement rejections, camera
conflicts or presentation-history corrections. Its one geometry wait lasted
17.84 ms. The grind run had five rail waits totalling 36.63 ms, maximum 9.12 ms.
Its only adapter snapshot was at the end: cumulative fault/rejection and history
correction counts were zero, but without an initial snapshot its deltas remain
unmeasured. Neither completed run encountered the new rail-bail witness.

The interrupted long test is neither a product-failure result nor a 300-second
pass. THAW's last observed ACK remained active; fault, movement-rejection,
camera-conflict and history-correction counters did not increase between its
two adapter snapshots. The source-gap high-water mark rose from 47 to 63 ms.
Only data before the last flushed pre-interference control ACK were aggregated:
nine geometry waits totalled 104.20 ms, maximum 17.51 ms; memory loading peaked
at 0.478 ms. Later human or idle frames are excluded. The harness steers directly
towards targets and does not route around obstacles.

Later, outside the interrupted test's measurement window, repeated GTA
barbershop interior/exterior transitions and automatic THAW re-entry exposed
a separate unresolved lifecycle issue. Two checked entries recorded fresh source
actors and zero GTA/source velocity, so the logs do not support retained
momentum or a new malformed-physics diagnosis; these later frames are excluded
from the controlled results.

In `perfcombined03`, the original source advanced from frame 6,723 to 6,724
with a 1.701 m Ground-to-Ground displacement during bail and no MotionContact
owner. The current request's detailed rejection record confirms reason 9 even
though the aggregate `hold_rejected` counter remained zero. Native exception
counters and presentation-history corrections did not increase; one camera
conflict was recorded, and the source-gap high-water mark reached 63 ms.
Nineteen geometry waits totalled 201.19 ms, maximum 33.91 ms. The complete
iteration peaked at 41.09 ms and memory loading at 0.439 ms. An earlier 1.294 m
rail-bail correction in that run was accepted by the existing witness, without
the new projection branch. All automatic recovery after the failure is excluded.

A separate Rail-to-Rail bail rejection in `perfmotion07` was investigated with a
pinned original-projection witness. It checked the original forward-node overshoot,
unchanged rail momentum, completed 1/60-second frame, owned straight level
nodes and matching bounded scripted sideways motion. The existing movement
allowance is unchanged. The recorded 2.102 m failure matches that formula;
a 480-frame original-engine run validated the same witness on a 1.895 m
correction. That run is not an identical replay of the failed live frame.

That forward-only witness did not cover reverse travel. The later captured
2.133 m reverse-rail correction received a separately checked original reverse
branch: its projection matches the retained source position within 0.001146
inches. The movement limit and projection tolerance are unchanged; 61 focused
checks and an independent 18-test rerun passed (`rail-reverse-proof.json`).

An opt-in original-code trace passed an 18-frame original-engine check, an
8-frame hooked check and 55 focused tests. This is diagnostic instrumentation,
not a Ground correction fix. The completed `groundtrace02` controller took
305.19 seconds including entry and final release. Its eight large-displacement
captures were all non-bailing; none was Ground-to-Ground. The capture budget was
exhausted at log time 146.67 seconds, or 142.64 active seconds, so missing later
captures do not prove an absence of later corrections. The full run retained
THAW until the final J press, but did not reproduce the specific unresolved bail.

The earlier **1.93 m Ground-to-Ground bail rejection from `grind03` and the
`perfcombined03` Ground rejection were not covered by that rail-only witness**.
The current bounded transport policy above replaces that acceptance approach;
the exact historical Ground failures have not been reproduced in its latest live
run. Occasional source, geometry and allocation pauses remain possible despite
GTA rendering at 120 FPS.

### Earlier source cadence and map-preparation checks

The earlier `urban02` test completed 300 seconds of continuous ordinary-input
driving plus braking: **18,073 original frames in 303.18 seconds, or 59.61 Hz**.
It reached seven waypoint targets and advanced past four others after their
45-second timeout; this is a completed duration test, not proof of a perfectly
followed rectangle. THAW remained active until the deliberate final J press.
Native fault, movement-rejection and camera-conflict counters did not increase.

With the eight-sample presentation history, that run recorded **zero forced
history corrections and a measured maximum correction of zero microseconds**.
There were 36,372 draw calls and 36,278 interpolated draws. The cumulative
publication-gap maximum increased from 47 to 79 ms, so the absence of forced
history jumps does not mean source samples arrived without pauses. Twenty
geometry waits totalled 243.0 ms, with a 34.9 ms maximum. Average work was
14.35 ms; the complete iteration peaked at 52.92 ms, and garbage collection
at 12.17 ms. No timed native resource call exceeded 20 ms. A valid original
rail-bail correction of 1.780 m and another safely ignored empty forecast were
observed; the rail witness used the existing segment, not the new corner branch.

The subsequent `grind03` test **failed** after 713 source frames and 12.39 active
seconds. It did record original grind contact, but a later Ground-to-Ground
position correction was rejected with reason 9. The backend released that
session and automatically entered a new one without another J press; the
later session is excluded from the failed test's measurements. That historical
failure predates the bounded transport policy described above. The successful
city route does not establish that every grind/bail transition is fixed.

Before that presentation-history change, the extended `perffinal05` route completed all 45 driving cycles without
an unexpected THAW release: 18,015 original frames in 302.05 active seconds,
averaging **59.64 Hz** including waits. Its 34 geometry waits totalled 320.3 ms;
the longest was **35.2 ms**. Native fault, movement-rejection and camera-conflict
counters did not increase. CJ received 18,014 presented source samples across
36,234 draw calls, of which 36,113 were interpolated. There were 43 presentation
history corrections; their count does not measure their visible size. The
source-gap high-water mark was already 62 ms at the start and remained 62 ms,
so it is not an independently measured maximum for this route.

The following `perffinalgrind02` ride completed 2,746 original frames in 46.28
seconds, averaging **59.34 Hz**. Original rail contact was recorded at tick
2,078, with the grind name and balance meter visible. Six rail-preparation waits
totalled 60.9 ms, with a **14.9 ms** maximum. No new native faults or movement
rejections were observed. Its counter comparison includes the inactive interval
since the previous route because this grind test had no separate initial
adapter snapshot. The old rejection record retained by the adapter belonged to
an earlier request and was unchanged in both successful runs.

A subsequent presentation audit reproduced a separate jump: after a renderer
pause, the four-sample history could discard the older endpoint still needed
by the interpolation cursor. The shared camera/body/board history now retains
eight samples, while cadence estimation and speed-change recovery still use
four intervals and the target remains 1.5 source intervals. This does not
change THAW's 60 Hz step, ordinary presentation delay or ownership/time guards.

All 288 bounded offline comparisons passed without a forced history jump;
the 18 steady/source-gap cursor hashes were unchanged. The complete native
build passed 17 suites, including the actual camera/pose/root/board regression
with a 75 ms render gap. These synthetic traces do not reconstruct the 43
corrections from `perffinal05`. After repeated stalls, preserving old endpoints
temporarily increases sample age instead of jumping forward: the largest
per-case mean increase was 11.49 ms and the largest per-case peak increase was
2.98 ms; the overall tested peak stayed 118.80 ms. A paused producer still
cannot supply missing frames, and source discontinuities beyond 100 ms retain
their reset behavior. The new diagnostic records the largest forced cursor
correction in microseconds; older logs have no such measurement.

The first live check, `perffinal06`, did not complete the requested route:
after 198.28 active seconds and 11,835 original frames, CJ entered water and
GTA's original swimming task took ownership. The adapter correctly ended its
movement lease; there was no trajectory rejection or original-worker fault.
The control script still records this as an incomplete route, not a stability
pass. It averaged 59.69 Hz, with no presentation rebases and a measured maximum
rebase size of zero. Geometry waits peaked at 25.8 ms; two original NodeArray
loads took 28.1 and 29.7 ms. One camera-conflict counter increment was also
recorded; it is not evidence that the camera caused the water handoff. A new
ordinary-input route steers between city waypoints to avoid unintentionally
entering the river; its completed `urban02` result is recorded above.

An earlier completed `perffinal01` ride kept THAW active for 141.55 seconds and advanced
8,427 original frames: 59.54 Hz, including geometry waits. An earlier 141.62-second
ride averaged 57.20 Hz. Explicit geometry waits fell from 4.006 seconds total
(longest 1.843 seconds) to 0.308 seconds total (longest 52.9 ms). These are
different bounded routes, not an identical replay or a guarantee for every map.
The ride ended with zero native fault counters and no rejected movement packets,
and did not activate slow motion. A further 46.29-second ride averaged 59.67 Hz,
confirmed original grind contact at source tick 479, and recorded 0.087 seconds
of geometry waits, with a 51.6 ms maximum. All native fault counters remained
zero; three bounded presentation-history corrections were recorded, which
does not measure their visual size.

Frame scheduling now retains its 60 Hz deadline through ordinary jitter,
recovering at most one tick without accumulating a backlog after longer stalls.
Native scene retirement batches checked
memory reads and reuses immutable reference classifications. Background tile
compilation submits at most two jobs to two workers. Tile centres are now 64 m
apart, with the same 64 m query radius and 16 m safety clearance; the game still
waits if the next required collision region is not ready. This reduces repeated
compilation and scene swaps without removing collision surfaces or weakening
current map, area, time or IPL checks.

After those two rides, a further staging adjustment moves the replacement
threshold to 36 m from the current centre, before its strict 48 m coverage edge.
This lets an outdated forecast be discarded and the correct nearby scene
prepared earlier. Offline tests retain the previous 24 m threshold for a 32 m
grid and verify coverage throughout a diagonal turn. The follow-up `perffinal02`
test **failed**: THAW mode was unexpectedly released after 120.83 seconds and
7,191 source frames, following movement-trajectory rejection reason 9 for an
airborne correction near a roof. The original solver subsequently reproduced
that inclined-ceiling correction using the actual roof geometry and recreated
entry position/velocity. The previous live actor matrix was not retained, so
this is not a byte-for-byte replay of the whole failed live frame.
Before the release, the run averaged 59.52 source Hz and recorded 16 explicit
waits totalling 156.3 ms, with a 23.6 ms maximum. All 16 waited for the original
rail graph; none waited for the collision region. This supports the staging
change's narrower purpose, but the failed route is not a stability pass.

The correction now requires a separate original head ray, agreement with the
original plane and velocity projection, and a second ray confirming clear
displacement. It is limited to adjacent non-bailing skating Air frames, a
maximum 2 m ceiling adjustment, and the existing transport speed/lifetime
guards. The source position and velocity remain unchanged. Product proof 45
passed on the real roof with the actor, source components, VM and scratch
guards unchanged; negative tests also cover missing/blocked rays, other
states, incorrect projection and a very shallow slope.

The subsequent `perffinal03` route also **failed**, after 110.94 seconds of the
control script. Its error was an empty speculative collision tile, not a native
movement rejection. The final accepted root was only 18.32 m from the current
tile centre, inside its 48 m safe coverage. Read-only reconstruction found 37
instances and 3,046 primitives in that current tile; the falling-motion forecast
64 m below it contained none. The old log did not retain the failed ticket, so
that forecast is identified by reconstruction, not a recorded ticket field.

Only the exact empty-scene compilation error for an optional forecast can now
be skipped, while an already validated current scene remains usable. The
desired required tile, loss of coverage, malformed results and database/rail
identity changes retain their errors. Failed forecasts are cached without
immediate recompilation and reported once per retained ticket/reason within
a bounded diagnostic cache. Offline tests replay the recorded position and
velocity, future-hour failure, later required selection and invalid compiler
results. The next `perffinal04` route also **failed**: after 42.06 active source
seconds it encountered a separate Rail-to-Rail movement rejection (reason 9).
This shorter route did not encounter an empty forecast, so it does not provide
live confirmation of its suppression.

The rail witness had tested both positions against the segment being left,
although the original bail had already moved to the next segment at a corner.
It now verifies that continuation through three reciprocally linked nodes in
the same owned static rail manager and source chain. Both adjacent frames must
be bailing with the same detached board. The existing 2 m correction allowance
remains: the new root must be within 0.2 m of the next segment, advance at most
1.02 m along the two connected segments and change height by at most 0.2 m.
Reverse travel and moving-vehicle rails do not receive this new allowance.
A 480-frame original-engine replay witnessed the 1.702 m corner correction,
including 107 rail frames, and exited cleanly; 28 focused tests passed. It uses
the existing correction flag and native bounds, with no new ASI change.
The extended `perffinal05` and subsequent grind route then completed as described
above. Neither encountered the new rail-corner witness, so its direct positive
evidence remains the original-engine replay. The long route did confirm one
unchanged-actor Wallride-to-Air correction against a flat ceiling. The grind
route also confirmed the optional-forecast fix in live gameplay: an empty tile
at `[2304, -1152, -64]` was reported once while the valid current tile at
`[2304, -1152, 0]` stayed active and the ride continued.

Compiler changes invalidate private compiled tiles once; comparisons between
these different routes are not matched warm-cache benchmarks.

During the isolated proofs, concurrent hosts exposed another startup issue:
the original file service reused two fixed Windows event names. The Rust host
now gives only those two pinned calls names containing its PID and actual
process creation time. Readiness checks require the same namespace and reject
older unisolated builds. Two concurrent hosts, alongside the existing backend,
each loaded all 113 original bank samples and completed 3,304 original calls,
including the product ceiling witness, then exited cleanly. This is bounded
evidence for event isolation, not proof that every startup timeout is removed.

On one recorded dense tile containing 79,759 triangles and 313 source spheres,
removing repeated scalar calculations reduced offline build/validation time from
2.97–3.11 seconds to 2.33–2.39 seconds. Comparison retained exactly equal COL
bytes, provenance, independent collision controls and validation seals under
the same compiler identity. The 2 cm sphere error bound is unchanged. A compiler
update invalidates compiled tiles once; the global map/grind index is reused.

Weighted work during `perffinal05` averaged 13.52 ms, with a 50.94 ms maximum;
the complete iteration including waiting peaked at 53.98 ms. Scene retirement
averaged 5.05 ms across 63 publications, with an 18.12 ms maximum. No timed native
resource call exceeded 20 ms in either of those successful routes. An earlier
diagnostic observed a 96 ms original NodeArray load, and failed `perffinal02`
recorded three loads of 22.2–23.1 ms. These larger calls did not recur in the
two runs. Garbage collection still reached 12.96 ms in the long route;
the shorter grind route had a 47.96 ms complete-iteration maximum. Occasional
engine, allocation or geometry stalls remain possible. GTA rendering at 120 FPS
does not by itself guarantee uninterrupted original animation delivery.

Two separate captured failures also have narrow corrections. A completed
original frame could be rejected when GTA advanced from 19:00 to 20:00 while
its successor collision tile loaded. The completed sample is now retained only
when unchanged ownership/activation and both old/current coverage checks permit
it; the original physics step is not repeated. Offline tests cover a changed
timed COL at that boundary. The `perffinal01` ride recorded a 04:00→05:00 handoff
with identical COL and continued past 06:00; it does not independently prove
the changed-COL branch in live gameplay. Separately, replay of the exact
detached-board failure now validates the hidden attached joints as source data
and applies rigid presentation limits to the verified board actually displayed.
Visible-board and CJ guards remain in force.

### Wall, audio and slow-motion update

The update is installed in the isolated local GTA copy. Its offline checks and
subsequent live skating tests cover different parts of the implementation.

- Hold **RMB** to aim with a gun in THAW mode, then press **Shift**. GTA world
  time and complete original THAW frames slow to 30%, entering over 0.2 seconds
  and leaving over 0.3 seconds. The entire effect lasts at most five real
  seconds; another Shift press or releasing RMB begins the exit. Mouse aiming
  retains its normal speed. Menu, pause, lost control or bail restores normal
  speed; another mod's owned time/pitch values are not overwritten.
- A subtle cool screen tint and two short synthesized entry/exit cues accompany
  the transition. THAW voices retain their original samples and authored pitch,
  multiplied by the current slow-motion scale. GTA's original SFX time-scaling
  path uses the matching smooth pitch; its original UI/unpausable exemptions
  remain. GTA's effects-volume slider controls all of the owned THAW host's
  audio sessions, including already-playing voices and exact zero mute.
- User log sessions 50/55/59 exposed a stock Wallride-to-Air ceiling correction
  whose vertical speed was a float32 residual, not exact zero. The three
  diagnostic witness gates allow at most 0.0001 inches/second while retaining
  the exact original velocity and all geometric/state checks. Replays test the
  numerical gate and transport; those old logs contain no original ceiling-query
  normal. The later long route confirmed one full original wall-release ceiling
  witness, but does not establish every wall interaction.
- Rail prewarming no longer repeats an idle VM census when no resource work is
  needed, or re-hashes the same verified worker result when committing it.
  Three real tile fixtures removed 1.14–2.45 ms of duplicate validation per
  matching region switch. The largest recorded 88/107 ms spikes were bracketed
  inside the original synchronous resource preload; this change does **not**
  establish their elimination. Geometry/source waits can still pause animation.

The Python suite counts and their revision scope are recorded above. The ASI's
17 native test executables and the Rust host's 9 tests passed in their respective
builds. Coverage includes the
five-second real-time limit, input edges, ownership
rollback, stale/foreign transports, source pacing, paired camera/body
interpolation, audio gain/mute, original pitch shadow and cleanup. The live
performance checks above do not establish every listening, effect or wall-move
interaction. Older gameplay evidence remains identified separately.

### Previously recorded gameplay checks

The Rust worker replacement passed a 180-frame original gameplay run, including
an Ollie and 166 evaluated poses. In live GTA, the new host passed a short grind
with 147 original rail frames, all four F8 tabs, manual balance, repeated J
switches, walking/remounting and camera recentering. The menu restored all 164
acquired input frames; movement, pose, board, camera and HUD fault counters were
zero in those runs. Process inspection found our Rust worker and no running
`THAW.exe` or reTHAWed process. Aerial input was exercised, but the new captures
do not establish a completed flip; the earlier aerial evidence below is separate.

Before the wall/audio update above, the full suites passed 491 GTA integration tests
and 371 original-engine tests, plus 16 native suites on the installed ASI.
The new transfer surface policy passed 180 independent rotation, translation
and scale cases, retaining convex rejection and face-order independence.
Separate package/installer checks passed without
system Python on PATH. Other detailed gameplay evidence below predates the Rust
host replacement unless stated otherwise; these bounded checks do not retest
every trick, map region or interaction.

The Sticker Slap investigation reproduced a rejected native bail palette: the
board bones contained a small scale/shear component. The board transport now
removes only that bounded cosmetic distortion around the original joint pivot.
Replay of the exact captured palette produced valid CJ and board poses; 96
already-rigid samples remained byte-identical to the previous implementation.
These are offline presentation checks, not a live retest of every wall move.

Separate stock-engine probes reproduced ceiling corrections during Air and a
Wallride-to-Air release, including the original 18/36-inch wall clearance.
Those probes used an original ceiling query and corresponding bounded source
states. Production transport has since moved to the consecutive-source-frame
allowance described above, preserving pending samples until acknowledgement.
The diagnostic probes left the original actor unchanged and exited cleanly. These checks
cover flat ceilings and downward corrections up to two metres; they do not
establish every Sticker Slap transition or replace live GTA coverage.

The new Num5 + Num8 + Space skitch gesture passed a separate 275-frame original
engine run using the final implementation. Holding Num5 first for five frames
then completing the chord grabbed the vehicle after 14 frames, without an
unwanted No Comply. Releasing the chord retained the stock animation and balance;
S released the grip. S plus the chord and a vehicle out of range caused no jump,
and a subsequent ordinary Ollie still worked. W alone did not grab. This is a
native gameplay proof; the new gesture still needs a live GTA traffic test.

The latest startup changes also passed a live test with 52 original grind frames
and a separate 14-second movement, skating/walking/board, camera and J sequence,
with zero native fault counters. The script ownership guard now accepts the
original engine's legitimate empty inline cache node; a disposable stock-engine
probe and 13 focused tests verified this case. Other malformed structures remain
rejected. These checks do not establish that all spontaneous restarts are fixed.

The latest contextual Space test in GTA recorded 20 skating frames with the
original `0x0c` transfer input, 97 walking frames and one original camera recenter.
Fault and camera-conflict counters did not increase. This checks the actual
walking/skating input path, not completion of a transfer. The same restart reused
the global grind index with all 118 map-source hashes reused and no map change.

Separate original-engine probes completed an Acid Drop on imported Glen Park
geometry and an exact Spine Transfer on a synthetic pair of quarterpipes built
by the same GTA collision exporter. The real-geometry Acid Drop run lasted 458
frames, including 194 grounded frames. The 118-frame synthetic
control displayed the original `Spine Transfer` award, landed on the target ramp
at frame 90 and continued down it. Both finished without native faults after
initializing the original particle factory used by landing effects. These are
native solver proofs outside the live GTA presentation, not proof of completed
transfers on every GTA ramp. A 168-frame flat-ground/standalone-wall negative
control produced no transfer. A subsequent live GTA check also passed the Space
walking/skating route with the particle-factory startup enabled. Particle effect
lifetime and cleanup after repeated Acid Drops have not been established by
these checks.

| Feature | Confirmed evidence | Remaining coverage |
| --- | --- | --- |
| Original gameplay and statistics | Our Rust worker executes stock THAW functions without creating a THAW game process. The new host passed 180 original gameplay frames with an Ollie and evaluated poses. Earlier native tests read all ten statistics as 10/10 and exercised cardinal/diagonal aerials through the original rotation solver. | Complete move-by-move parity has not been established. Both original games remain required. |
| CJ, board and animation | Latest GTA visual test displayed CJ and the board performing a backflip. The HUD showed `TAILGRAB + BACKFLIP` and a red 3,100-point failed combo after the low jump. Native throw/retrieve tests also verified the detached board's moving transform and return. | Broader animation, landing, bail and detached-board presentation coverage. The visual test is not proof of a successfully landed flip. |
| J, walking and camera | Repeated mode changes, current-position entry, momentum transfer and Space recenter have bounded live evidence. Latest walking test confirmed skating → walking → skating. | Extended rides, map transitions and more camera/parkour situations. Geometry waits can still pause the source simulation. |
| F8 and trick assignments | GTA menu tests passed pause/resume, aerial OFF/ON and restoring edited bindings. All four tabs were visually checked; the latest test matched all 164 control acquisitions with restorations and reported no conflicts. Original normal/special assignments passed native readback, including edits during a flip. | The full THAW Created-a-trick editor is not implemented; existing created-trick entries remain read-only. |
| HUD and sound | Score/trick text and combo timer have live evidence. Latest GTA captures show original manual and grind balance meters with `Switch Manual` and `Switch BS 5-0`. Native tests verified all 31 extra samples, bringing the registered bank to 144, with exact PCM and loop flags. | Broader special/HUD state coverage and listening checks for every contact/sound category. |
| Static map, rails and wallrides | World/rail indexing, persistent tile caches and English preparation notices are implemented. The short GTA rail test passed 152 original grind frames, balance HUD, bail/recovery and manual exit. A separate 45-second ride retained THAW mode over about 330 metres, including confirmed rail contact. A native wallride test entered the original wall state for 15 ticks. | Broader route/transition coverage remains necessary; not every visible edge qualifies. The 45-second ride does not establish that every map region is free of pauses. |
| Board strike damage | A real walking board strike reduced a tracked GTA pedestrian's health from 100 to 75. Native diagnostics recorded one swing, one hit and zero damage faults. On the current build, an actual NPC hit reduced CJ's health while the same THAW session remained active through recovery. | Other pedestrian states and melee edge cases have not all been exercised. |
| GTA guns in THAW mode | Live tests cover pistol/AK fire, reload, broad camera sweep, walking, skating and airborne movement. Actual NPC health loss was confirmed with pistol/AK on foot and AK while skating. A hanging test passed 4 shots and 49 matching hanging observations; the corrected camera kept CJ visible. No native faults were recorded. Walking direction follows the aiming camera after 90/180-degree turns, and the head tracks the actual crosshair hit. | More ledges, hanging transitions, near-wall behavior, launchers and remaining weapon classes need live coverage. |
| Moving vehicles | Original-engine tests cover actual GTA car collision shapes, contact after translation/rotation, moving grind, skitch and safe owner removal. Live GTA traffic produced 414 original skitch balance frames; an NPC-driven car pulled CJ almost 28 metres, followed by an ordinary release with S. A separate run without balance corrections reached the original meter limit and released the grip while THAW mode stayed active. | A grind on an actual car in live traffic still needs separate live coverage. New shapes need brief background preparation; oversized shapes are rejected individually. |
| Local installation and packaging | A fresh standalone package completed the full local install: 415 GTA files, original runtime/scripts/profile/assets, HUD atlases, extra terrain audio and three verified dependencies. Read-only launcher validation and source/copy hashes passed. Private-runtime launcher tests, source-package import checks and Rust host startup/gameplay checks passed separately. | The full fresh import test used an earlier backend. First game launch and cold map-cache preparation on that fresh copy have not been exercised; the new Rust host was tested in the existing isolated GTA copy. |

The static importer does not reconcile moving/destructible objects or virtual
Mod Loader geometry overrides. Multiplayer, custom ladder authoring and full
Created-a-trick editing are outside this implementation. The configuration
targets 120 FPS; it does not guarantee that frame rate or eliminate every pause.
One recorded session containing several tests had 36 source-buffer waits,
totalling about 0.60 seconds; the longest was about 79 ms. Those measurements
cover explicit geometry waits, not every possible rendering or animation hitch.

Camera, CJ, the board and upper-body blending now use a shared high-resolution
QPC clock. Presentation adapts to the measured arrival rate of source samples,
retaining a bounded interpolation buffer when the worker runs below 60 Hz.
The original physics step remains 1/60; presentation never extrapolates beyond
published samples. Independent 60-second simulations covered 36 combinations
of source 50/55/60 Hz, rendering 120/144/240 FPS, coarse packet timestamps and
jitter. After startup they recorded no presentation holds or history rebases.
The maximum equal-time camera/actor phase difference was 1.919 ms without
accumulating drift. Verified native FollowPed resets retain camera history and
aim direction; explicit cancellation and new ownership still clear them.

The final ASI passed a 23-second live sequence with two J entries/exits, camera
rotation and walking: 2,505 interpolated renders from 1,242 source samples and
no increases in eight native fault counters. One verified GTA camera reset
recovered after one render wait. Four bounded history corrections occurred;
their counter does not measure their size or establish a visible hitch.
A separate 22-second live check explicitly verified skating, walking and
remounting, with 485 skating and 115 walking frames retained in the source
capture, 1,522 interpolated renders and zero increases in those fault counters.
The captures show CJ pushing on the board, carrying it while walking and
returning to a riding pose. These are bounded checks, not a guarantee of
perfect animation in every state.

Caching immutable rig calculations reduced a local pose benchmark from 4.36 to
2.47 ms, with 1,048 exact output comparisons. Caching only the parsing of
unchanged IPL bytes retains all fresh reads, code guards and both snapshots.
Across two different bounded live routes, world/coverage work fell from 3.136
to 1.365 ms and overall frame work from 17.101 to 14.901 ms; source cadence was
57.22 and 59.15 Hz respectively. These are local measurements, not an identical
replay or a guarantee of 60 Hz everywhere. One new vehicle shape caused a
16.46 ms vehicle-stage spike; background compilation does not eliminate every
cost of registering a shape in the original engine.

One earlier live attempt timed out while loading an original rail NodeArray.
This has not been identified as fixed. A disposable original-engine stress
test completed 64 exact rail-resource replacements with 16 vehicle NodeArrays
and four full ownership lifecycles; load median/max were 1.41/5.83 ms with the
500 ms timeout unchanged. Failure-only diagnostics now capture the owned
worker state if this rare timeout returns. The integration retains its
failure checks rather than continuing an unresponsive original-engine call.

## First run

1. Extract the complete **integration package** into a writable folder. It
   includes a private Python runtime with Tkinter and our compiled Rust worker;
   there is no Python, Rust or Visual Studio installation step for players.
   Keep the `runtime` and `build` folders beside the launchers.
2. Open `Configure-GTA-THAW.cmd`.
3. Select your original GTA San Andreas installation and original PC THAW
   installation. Selecting THAW's parent directory is supported when the game
   is installed in its `Game` subdirectory.
4. Choose a **new** isolated destination, for example `D:\Games\GTA_THAW`.
   Existing folders and original game files are never overwritten. Use a short
   ASCII output path. The installer checks free space before copying.
5. Select **Install local mod**. Progress, cancellation and errors appear in the
   setup window. Cancellation during file copying stops between chunks; an
   atomic asset importer finishes its current operation before stopping. An
   incomplete output remains visible for inspection and cannot be launched by
   the mod launcher. Choose a new destination for a subsequent attempt.
6. Open `Run-GTA-THAW.cmd`, start or load a GTA game, and wait for
   `Map ready - press J to enter THAW mode.`. During startup,
   `Preparing map for grinding...` covers map work and `Starting THAW gameplay...`
   covers original-engine initialization. Map preparation alone does not mean
   the engine is ready. The map and grind data are cached locally.
   Press **J** to enter or leave THAW mode. Press **F8** for settings.

The selected installation is stored in `installation.json` beside the launcher.
`Run-GTA-THAW.cmd --game "D:\Games\GTA_THAW"` explicitly selects another prepared
copy. The previously prepared local `I:\GTA_THAW` remains a compatibility fallback
when there is no first-run selection. Keep the integration folder and both
original installations available: input manifests still verify local provenance.

Both `.cmd` launchers prefer `runtime/python/python.exe` and ignore Python
environment settings and user packages. This private runtime does not install
anything globally or change PATH. An incomplete integration bundle reports an
error instead of silently selecting another interpreter. A **source-only**
checkout still needs 64-bit Python 3.12+ with Tkinter and the Windows `py` launcher,
plus the build tools described below.

On this installation, checking the unchanged map fell from **2.325 to 0.189
seconds**: all 118 source inputs reused validated hashes, with no source bytes
rehashed. Reuse requires unchanged NTFS/ReFS file identity, size, write time and
Windows ChangeTime while the file is open. Changed inputs and systems without
those checks use full hashing; executable verification never uses this cache.

The original THAW resources and private resource copy still receive full SHA-256
verification. A single validated walk per inventory and two independent workers
reduced the local check of 7,925 files in each copy from **19.25 to 3.33–3.48
seconds**, preserving the same files and digests. The measured complete warm
backend restart took **9.23 seconds**, including old-backend cleanup while GTA
was already running. This is not a cold GTA launch measurement. First map
preparation, modified maps, storage speed and hardware can take longer; loading
is not instantaneous.

The native adapter requires the supported GTA 1.0 US executable fingerprint:
`a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26`.
The original THAW executable fingerprint is:
`ff3351529586eefba74d85a807282e18101844e68ecaa5cee20a0a16bed0b118`.
Scripts and required DLLs/assets are checked separately. A matching file name
or a different unmodified retail edition does not establish compatibility.
reTHAWed installations are not accepted by this stock backend importer.

## Installed locally

Setup copies the base GTA directories into the new output and adds our ASI.
It imports original THAW scripts, runtime inputs, profile, skeleton, animations,
board, fonts, HUD textures and sound resources into private `thaw_gta` caches.
The HUD atlas is built directly from local stock resources; no developer's
recorded gameplay or research directory is needed. The first GTA launch prepares
supported static map geometry and rails, retaining the existing preparation
notice in the upper right of the game.

The original global sound bank is supplemented with 31 unchanged samples from
two locally verified stock THAW zone banks (144 registered samples in total).
Only hashes and extraction metadata ship with the importer. Older private caches
are upgraded before starting the hidden backend; a missing or changed original
installation produces an explicit error instead of loading unverified audio.

The GTA copy has its own save/profile directory. Setup does not copy existing
GTA save games or arbitrary ASI plugins from the source installation. Existing
on-disk maps are copied with GTA's base data; virtual Mod Loader substitutions
are not interpreted by the map importer. Do not rename the output folder after
import: its private manifests contain the validated absolute paths.

Settings are stored in `<isolated GTA>\thaw_gta\settings.json`. First-run defaults
enable aerial flips, retain original THAW physics and maximum skater statistics,
disable lowered grind camera, and use the existing keypad skating controls.
Space recenters the camera while walking; while skating it sends the original
L1 + R1 SpineTransfer request. The original engine decides whether the surface
and movement allow a transfer. Existing remapped `CameraRecenter` settings retain
their key and use the same walking/skating context. F8 labels that binding
`Camera (walk) / Spine transfer (skate)`. F8 supports changing controls and original trick
assignments; reserved keys and conflicting combinations are rejected.

F8's Gameplay tab also offers **Infinite grind / manual / lip balance**, disabled
by default. It enables the original THAW perfect-balance flags, so the meter
continues moving but reaching its end cannot cause a balance bail. Collisions
can still cause bails; skitch balance is unchanged. Turning the option off restores
normal balance. The setting is saved, and older settings are migrated without
losing bindings or trick assignments. An isolated original-engine test covered
both meter limits for manual, nose manual, grind, slide and lip, then verified
normal manual/grind failure after disabling it. Live GTA testing is separate.

The current adapter transfers the original whole-body aerial rotation and
detached board motion as well as the bone animation. Suitable vertical static
faces receive THAW's wallride flag; the original gameplay still decides whether
the approach qualifies. Destructible objects and custom ladder metadata remain
outside the static world importer. Moving vehicles use a separate live bridge:
the adapter reads GTA's actual collision primitives and current transforms,
prepares local shapes asynchronously, and supplies original THAW moving owners.
Vehicles are never baked into the persistent map cache. Nearby cars can supply
original collision, qualifying body-edge rails, and rear skitch hooks. Approach
an NPC-driven car from behind on the board and hold **Num5 + Num8 + Space**
together briefly (the original grip check takes about 0.2 seconds). These are
the current Ollie, Grind and CameraRecenter bindings; rebinding those actions
also changes the chord. Forward (W) alone no longer requests a grip. Once
attached, release the chord and balance with A/D; backward (S) releases it.
THAW owns the grip,
balance meter, animation and loss of balance. Skitch hooks require a living
vehicle with a driver. Steering the vehicle remains GTA's responsibility.

The bridge observes up to 16 nearby vehicles within 80 metres and prepares one
new native owner per update. New models can briefly await background preparation;
this does not pause skating or rebuild the map. Rails come from qualifying
collision edges, so a visible seam or every car roof is not automatically a rail.
Native proofs cover moving collision on three captured GTA car shapes and
original skitch/grind behavior on a controlled moving fixture. The latest live
traffic test produced 414 original skitch balance frames, with the NPC-driven
car travelling 27.75 metres during the five-second observed towing interval.
Ordinary A/D inputs maintained the original balance; S released the grip.
The corrected local vehicle axes put CJ behind the bumper while preserving
world-space collision and rail geometry. In a separate live run without A/D
corrections, the original balance approached its 4,000-unit limit and released
the grip while W remained held; THAW mode stayed active. These tests prove live
towing and balance-driven release, not a live car grind.

Local vehicle collision has a separate budget of 1,024 original collision
parts, 51,200 triangles and 4 MiB per shape. The existing sphere approximation
keeps its maximum 2 cm error. A read-only audit covered 211 embedded stock
vehicle collision models; AT400 exceeds this budget and currently has no
THAW collision. Unsupported shapes are logged individually, and a full native
shape cache defers preparation instead of ending the skating session.
Neither case changes the persistent static map index. The larger native
budget was verified with an actual GTA train shape, including contact,
rotation and the original deleting destructors.

J also accepts the idle legacy state GTA leaves behind after releasing a
sniper scope. It still requires the native weapon camera mode to be cleared
and all normal actor, task, health and ownership checks to pass; it does not
overwrite CJ's native state to force entry.

The importer adds THAW BANK flags to upward-facing slopes from 25 degrees to
below 85 degrees, and VERT flags to connected concave ramps spanning shallow
and steep sections. Flat ground, ceilings, standalone walls and sharp corners
are excluded from transfer ramps. Classification uses final float32 geometry
with a bounded tolerance for nearly coplanar triangle pairs. The original solver
decides whether a specific approach can transfer; not every visible ramp or map
surface qualifies. This policy updates cached collision tiles without rebuilding
the separate global grind index on an unchanged map.

Keep **Num Lock enabled** for the keypad preset. Keyboard trick bindings can be
changed under F8, except the mode/menu and reserved system keys. The weapon
mouse gestures below are currently fixed:

| Key | Default action |
| --- | --- |
| J | Enter / leave THAW mode, retaining position and momentum |
| F8 | Open / close settings; THAW simulation pauses while the panel is open |
| W / A / S / D | Movement and trick direction |
| Num5 | Hold to charge an ollie; release to jump |
| Num4 / Num6 / Num8 | Flip / grab / grind |
| Num5 + Num8 + Space | Hold briefly behind an NPC-driven car to skitch; A/D balance, S releases |
| Direction + Num6 in the air | Aerial flip or roll; diagonals are supported |
| E | Get off / back onto the board |
| Num6 while walking | Swing the board and damage a pedestrian within reach |
| Space | Walking: camera behind the character; skating: original SpineTransfer request (L1 + R1) |
| Num / and Num * | Rotate the original THAW camera |
| Num7 / Num9 | Rotate left / right; together request a spine transfer |
| Num0 | Nollie modifier |
| Hold right mouse button | Aim the selected GTA gun while remaining in THAW mode |
| Left mouse button while aiming | Fire; the left button alone does not fire |
| Mouse wheel | Select another available weapon from CJ's inventory |

Weapon integration has passed the x86 build, unit checks and controlled live
GTA tests with 9 pistol shots and 51 AK shots. The AK test included a reload
beyond its initial 30-round clip, a broad camera sweep, skating, walking and
airborne movement; neither test recorded native faults. No gun or ammunition
is granted by the mod. The selected gun
rests on CJ's back, muzzle down, until he aims. A scoped upper-body pose follows
the aim direction while the original THAW leg animation and movement continue,
including jumps and falls. Releasing the right button blends the upper body
back to THAW. A carried board moves onto the back while aiming on foot; an
already detached board keeps its original trajectory.

While hanging, CJ keeps his original left-hand support and uses only a
compatible one-handed gun. Aim is bounded to 95 degrees on either side and
60 degrees up/down. Only one physical gun is used, including with native
dual-wield skill entries. Two-handed weapons can still be selected while
hanging but cannot aim or fire until the character leaves that state. A bounded
live test passed 4 shots and 49 fresh, matching hanging observations with
advancing source simulation and no faults. Captures showed CJ in the corrected
camera view, with his left hand supporting him and his right hand holding the
gun. Other ledges and hanging transitions still need coverage. Native
GTA firing functions handle ammunition, reload timing and damage. Follow-up
tests measured actual NPC health loss from pistol and AK fire on foot (100 to
75 and 100 to 67), and AK fire while skating (100 to 70). The muzzle converges
on the first actual collision under the crosshair, and CJ's neck/head follow
that point without moving his support arm. Walking WASD follows the aiming
camera: forward movement passed checks before and after 90/180-degree turns.
Skating steering and hanging shimmy retain their original control basis.
Launcher support uses the native projectile path with a scoped compatibility
adjustment; near-wall behavior and every weapon class still need broader
coverage. Validated buffering heartbeats preserve the selected weapon and aim
pose; firing waits for an unbuffered source sample. Bail, death, the F8 menu,
loss of focus or loss of movement ownership reset weapon input; ordinary
airborne movement does not. After one of those resets, release the left button
before firing again. A normal buffering pause does not require another click.

If the source skater was walking when you left THAW mode, J now returns through
the complete original walking-to-skating script route. It restores the board,
animations, exceptions and looping sound together with physics. A native
regression exercised 503 frames, including an airborne return with unchanged
position/velocity and two grounded return cycles; the board returned beneath
the skater. The full stock-backend Python suite passed 330 tests. This check
used the original engine separately from GTA presentation.

Inside F8 use Tab or Left/Right to select a section, Up/Down and Page Up/Down
to move through entries, and Enter to edit. The Tricks and Specials sections
show combinations using the currently assigned keys. Settings are saved after
each accepted edit and applied when the panel closes. Restoring defaults needs
two confirmations in the panel. Skater statistics remain fixed at **10/10**;
there is no switch to another game's physics.

Setup downloads exactly three pinned dependencies from their official GitHub
releases, verifies archive SHA-256 values, and installs their MIT license files:

- [Ultimate ASI Loader v9.7.4](https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases/tag/v9.7.4)
- [High FPS Fixes v1.9.0](https://github.com/sonochiwa/sa-high-fps-fixes/releases/tag/v1.9.0)
- [Windowed Mode v2.2](https://github.com/ThirteenAG/III.VC.SA.WindowedMode/releases/tag/v2.2)

The selected configuration targets 120 FPS and borderless windowed display.
Actual frame rate depends on the hardware and scene. Existing incompatible
input fingerprints or dependency downloads fail explicitly; the installer does
not download GTA, THAW, cracks or replacement game executables.

Offline setup accepts the three already downloaded and verified archives:

```powershell
.\Configure-GTA-THAW.cmd --gta "D:\Games\GTA Original" `
  --thaw "D:\Games\THAW Original" --output "D:\Games\GTA_THAW" `
  --dependencies "D:\Downloads\GTA-THAW-dependencies" --offline
```

Required cache filenames are `Ultimate-ASI-Loader-v9.7.4-NoPDB.zip`,
`HighFpsFixes-v1.9.0.zip`, and `III.VC.SA.WindowedMode-v2.2.zip`. The code pins the
archive hashes; manually renaming a different version does not make it valid.

## Code and packages

`tools/gta` contains the host adapter, map/rail importer, pose conversion,
camera/HUD/audio transport, GUI, controls, launcher and local installer.
`tools/gta/native` contains the x86 ASI and guarded transport/render hooks.
`tools/gta/runtime_host` contains our 32-bit Rust executable that maps the local
stock THAW image, resolves its supported dependencies and exposes the process
used by the original-function integration. Its build metadata pins the executable
and source files. The original THAW entrypoint is not used. This host is required
for gameplay and differs from the optional observation tool below.
`tools/gta/host` contains the complete independent Rust/Bevy observation tool:
its two Rust source files, Cargo manifest, dependency lockfile, build helper and
README. It reads GTA telemetry and is optional for development; skating runs
through the native adapter and original THAW integration described above.
Build or test this crate from its directory with `cargo build --locked` or
`cargo test --locked` on Windows. Cargo obtains dependencies separately; neither
vendored dependencies nor generated `target` files are included in the package.
`tools/vanilla_thaw` contains the original-image integration and input validators;
its small JSON files describe compatibility checks rather than game resources.
The handful of helpers under `tools` and `tools/waw` decode local original file
formats and are reused by the GTA importer.

Our integration source is under the repository's MIT license; dependency
licenses are in `tools/gta/licenses`. That license does not grant rights to GTA
or THAW content. This packaging workflow does not provide a legal determination
about game-file modification or compatibility techniques.

Build our ASI, runtime host and atomic transport from a source-only package using Visual Studio
C++ x86/x64 tools, Rust and the `i686-pc-windows-msvc` target:

```powershell
.\tools\gta\native\build.ps1
.\tools\gta\runtime_host\build.ps1
.\tools\gta\transport_native\build.ps1
```

The builds run their native tests. A regular integration package includes our
compiled ASI, Rust runtime host, atomic transport DLL, build manifests and private CPython/Tk runtime.
A source-only package excludes those binaries and requires all three builds before
setup. Neither package contains GTA plugin binaries, original
game executables, extracted scripts, animations, textures, audio, world databases,
private SDKs or research captures. The installer creates these private caches
only on the user's computer. The private Python runtime is copied from a
checksum-pinned official Python Windows archive; its original licenses and
`PYTHON_RUNTIME.json` are included under `runtime/python`.

Run these commands from the integration/source folder. First inspect the
source-only selection without creating an output directory:

```powershell
py -3 -B tools/gta/package.py --source-only
```

Create a new source package, or create an integration package after building
the current native ASI, Rust host and x64 atomic transport:

```powershell
py -3 -B tools/gta/package.py --source-only --output dist/GTA-THAW-source
.\tools\gta\native\build.ps1
.\tools\gta\runtime_host\build.ps1
.\tools\gta\transport_native\build.ps1
py -3 -B tools/gta/package.py --output dist/GTA-THAW-integration
```

Each output must be a new directory; the packager refuses to overwrite an
existing one. The regular package checks all three native build manifests before
including the binaries. It downloads the pinned Python archive when necessary;
`--python-cache PATH --offline` requires an already verified local archive.
Both variants include the installer, launchers, this guide
as their README, integration sources and third-party notices. Neither includes
the user's game files or third-party GTA plugin binaries. Source-only setup requires
building all three native components first; an output directory alone is not an installed GTA mod.

`PACKAGE_MANIFEST.json` records the selected paths, sizes and hashes. Review and
upload that prepared source/integration directory, rather than the working
checkout, installed games or private `thaw_gta` caches. Package creation does
not certify the remaining gameplay and fresh-copy launch checks listed above.
