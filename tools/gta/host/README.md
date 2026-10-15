# GTA THAW observation host

This standalone Rust crate reads the isolated GTA ASI's `GTATHAW2` shared-memory
V2 protocol on a headless Bevy schedule. It does not depend on the legacy CoD host,
the retained reTHAWed worker, private trick catalogs, or THAW game files.

This is a developer observation tool. It does **not** enable skating, change the
player, or control the camera. Every successful report states
`"skating_available": false`.

Build with Rust stable for Windows and the MSVC C++ build tools. The PowerShell
helper accepts explicit shared dependency caches and a separate GTA build target:

```powershell
.\build.ps1 -CargoDirectory 'I:\THAW_BevyBuild\cargo' -RustupDirectory 'I:\THAW_BevyBuild\rustup' -TargetDirectory 'I:\GTA_THAW_Build\target' -Action test
.\build.ps1 -CargoDirectory 'I:\THAW_BevyBuild\cargo' -RustupDirectory 'I:\THAW_BevyBuild\rustup' -TargetDirectory 'I:\GTA_THAW_Build\target' -Action build
```

Those paths are local examples; use your own caches and output directory. The
helper is offline and requires dependencies to be cached already. Ordinary
`cargo test --locked` / `cargo build --locked` also work from this crate directory.

Start gameplay in the isolated GTA installation with the ASI loaded, then run:

```powershell
& 'I:\GTA_THAW_Build\target\debug\gta-thaw-host.exe' --gta-observe --gta-pid 1234 --seconds 5
```

Replace `1234` with the GTA process ID. The duration defaults to 10 seconds and
must be positive and at most 60 seconds. The host checks the inspected GTA EXE
hash, process lifetime, isolated profile path, protocol layout, seqlock, and
snapshot age. Invalid, stale, missing, or regressing data stops observation with
a nonzero exit code. Loading screens or menus without published state may also
stop it; start the tool once gameplay is running.

The final JSON contains first/last snapshots, elapsed time, distinct sequences,
scene changes, the actual GTA clock, control/cutscene masks and ped motion bits.
`control_eligible` excludes paused, cutscene, control-disabled, dead and vehicle
states; it does not require the ped to stand on the ground. `grounded` comes
directly from the native standing bit. Neither value means THAW is ready.
Invalid clock/mask values are rejected. The reader requires the V2 mapping
`Local\THAW_GTA_STATE_V2_<pid>` and never falls back to V1.
The raw clock bounds are 23/59/60: original `CClock::Update` can publish second
60 for the exact elapsed-time boundary before incrementing minutes. This is
preserved without advancing the hour; seconds 61 and above remain rejected.

Positions are GTA world coordinates with Z up. Velocity and
timer step are raw GTA values; no THAW conversion or assumed metres-per-second
conversion is applied. The observer requests only read access to the mapping
and process query/synchronization rights.
