# Stock THAW bootstrap tools

The friction proof is a separate, dependency-free Rust executable for Windows x86. It verifies
and calls **two whole original functions** from the user's local THAW.exe.
It is not a complete skating simulation, vanilla backend, or GTA integration.

The source contains hashes, addresses and tests, not copied game machine code.
Supported EXE: 21,442,560 bytes, SHA-256
`ff3351529586eefba74d85a807282e18101844e68ecaa5cee20a0a16bed0b118`.
The inspected user's new stock installation and the old THAW.exe have that same
hash. This pins an observed input, not a claim of authenticated retail provenance.

The host reserves owned memory at an OS-selected address and commits only one
code page plus two constant pages, between inaccessible guard pages. It imports
715 original function bytes and five original float constants. Exactly seven
audited absolute constant-address operands are relocated to the owned constant
pages; every other function byte must remain unchanged. The report records each
relocation and the original and relocated function hashes. Unused code bytes trap;
everything else stays non-executable or inaccessible. No PE entrypoint, import
initialization, `.rld`, `.init`, original process or protection-related code runs.

Both functions were statically decoded in full. They contain no calls and no
branches outside their own function. Their only absolute reads are the five
pinned constants. Original object offsets and instruction sequences match the
already tested reTHAWed friction leaves except for the constants' addresses.
The two functions write only the owned skater velocity vector; fixtures have
guard pages and canaries. Exact relocated code bytes and constant values are
rechecked before and after every native call. The first fixed-base implementation
built successfully but could not reserve the original range, so it made no native
calls. Operand relocation removes that dependency on host address allocation;
it does not replace the original friction instructions with a new solver.

Build with an installed Rust MSVC x86 target and C++ linker:

```powershell
cargo test --offline --manifest-path tools/vanilla_thaw/Cargo.toml --target i686-pc-windows-msvc
cargo build --offline --release --manifest-path tools/vanilla_thaw/Cargo.toml --target i686-pc-windows-msvc
```

Run the resulting `thaw-vanilla-proof.exe` with two absolute paths:

```text
thaw-vanilla-proof.exe "<your THAW Game directory>\THAW.exe" "<new local report path>.json"
```

An existing report is refused. The input is read-only. Success requires sixteen
control cases, each executed twice with bit-identical outputs, expected numeric
results and intact memory guards. Invalid inputs are rejected before a native
call. The report contains measurements and hashes; no game code is exported.

Build and native execution must be verified separately. A passed result proves
only these friction leaves; it does not establish gravity, collision, tricks,
animation, score, audio, HUD, or gameplay parity.

## Private stock core QB import

`import_qb.py` prepares the original compiled scripts as a separate local input
for future backend work. It consumes the source configuration created by
`tools/gta/setup_sources.py` and imports **only** `data/pak/qb.pak.wpc` together
with `qb.pab.wpc`. It never executes THAW code or loads a reTHAWed mod archive.

```powershell
python -B tools/vanilla_thaw/import_qb.py --sources "<local sources.json>" --output "<new private cache directory>"
python -B -m unittest discover -s tools/vanilla_thaw -p test_import_qb.py -v
```

The selected THAW executable and both archives must match the pinned observed
fingerprints. Their hashes are checked against the saved configuration before
reading and again before publication. This data-only import does not rehash GTA's
multi-gigabyte archives. A different THAW build needs its own inspection and
fingerprint support; changing the source configuration alone cannot bypass this
check. The fingerprint is not proof of authenticated retail provenance.

The cache contains `members/0000.qb` through `members/0267.qb` and `manifest.json`.
Flat numeric filenames preserve archive order without turning game paths into
filesystem destinations. The manifest retains original and normalized names,
all eight header words, checksum identifiers, source offsets, byte lengths and
SHA-256 hashes. Payload bytes are preserved exactly, including layouts which
the existing semantic QB reader does not understand. Bounds, unexpected member
names, traversal, duplicate logical names/checksums and overlapping payloads
are rejected. The header walk also verifies each result with the existing
checked `carentan_qb.read_member` archive reader; it does not invoke its
map-specific semantic compiler.

The Windows importer refuses existing destinations and either original game
directory. It writes into an exclusively created sibling staging directory,
verifies the written members, revalidates sources and publishes by an exclusive
directory rename. An error removes only its own verified staging directory.
The original files are opened for reading only.

Read-only validation of the selected stock pair found **268 members containing
2,423,404 payload bytes**, from `scripts/zone_sizes_wpc.qb.wpc` to
`scripts/aaaaloopchecker.qb.wpc` in original order. The PAK uses 32 bytes of
`0xCD` padding after its terminator; this is accepted and its hash is recorded.
Twelve synthetic tests cover order and byte preservation, invalid paths,
duplicate names, header bounds/overlap, source changes, pinned identities,
exclusive publication and cleanup after failures.

The importer source can be kept with the project's own code. Its generated
cache contains original game script bytes and must remain private; it is not an
open-source replacement for those scripts. No animation/audio closure is
inferred. Both `backend_ready` and `complete_asset_closure` stay **false**.

## Private native inputs and loader experiment

`prepare_image.py` uses the same saved source selection to create a separate,
verified copy of `THAW.exe`, `binkw32.dll` and `d3dx9_27.dll`. These three pinned
files total 24,139,984 bytes. It refuses existing destinations and original
game directories, preserves input files and verifies hashes before publication.
It does not launch a process. Four synthetic tests cover independent copies,
version mismatches, original destinations and failure cleanup.

```powershell
python -B tools/vanilla_thaw/prepare_image.py --sources "<local sources.json>" --output "<new private native directory>"
python -B tools/vanilla_thaw/probe_loader.py "<absolute native directory>/manifest.json" "<absolute new report>.jsonl" --allocator
```

The second command is a bounded **development experiment**, not a game launcher
or playable backend. Windows creates a hidden process from the private original
EXE; its primary thread remains suspended throughout. Our separate thread can
call selected original functions after normal OS library initialization. This
does create a process from THAW's image; it does not call the game's EXE entrypoint
or start its game loop. The helper imposes a ten-second deadline and terminates
its own child afterward, also on failure.

The observed allocator test passed: the original memory manager initialized its
expected singleton and a 256 MiB arena belonging to the worker's private heap.
The three checked native functions were unchanged after the call, and the child
was terminated successfully. The earlier standalone Rust raw-image experiment
could not reserve THAW's original address range; letting Windows load the image
resolves that specific conflict. This result proves allocator startup, not QB
execution, skater simulation or a complete Rust port. Current gameplay setup
and validation status are in [the integration guide](../../docs/gta-first-run.md).

Generated native copies and QB caches contain original game material. Keep them
outside the public source package; other users must import their own inputs.

The next bounded loader mode accepts the private QB cache:

```powershell
python -B tools/vanilla_thaw/probe_loader.py "<absolute native directory>/manifest.json" "<absolute new report>.jsonl" --qb "<absolute QB directory>/manifest.json"
```

The observed test passed in 20 ms, with its child terminated by 22 ms. Original
functions initialized the checksum heap, name registry and symbol table, loaded
the exact 48-byte `scripts/game/cas_team.qb.wpc` member, returned its integer
value of 1 at the expected source-buffer address, and unloaded it. All 4096
symbol buckets were restored and the checked native function/entry bytes stayed
unchanged. The main thread remained suspended. This is native QB data loading,
not execution of a script body; no skating or gameplay parity is implied.
See [the loader implementation](probe_loader.py) for this bounded bootstrap.

The `--script` mode repeats that scalar check before preparing the original
Script and two Script-cache heaps, ScriptContext and ScriptCache:

```powershell
python -B tools/vanilla_thaw/probe_loader.py "<absolute native directory>/manifest.json" "<absolute new report>.jsonl" --script "<absolute QB directory>/manifest.json"
```

It loads the whole original `scripts/game/net/xboxlivemenu.qb.wpc` member and
executes only `xboxlive_menu_players_is_player_muted`, a bounded script returning
the unnamed `FALSE` checksum. The game's cache handles its compressed body;
the host neither decompresses nor replaces it. One native CScript update must
finish and return the expected checksum through the original CStruct getter.
Native destruction, freeing and source unloading must then restore the empty
script registry and all 4096 symbol buckets. Heap/context/cache objects remain
owned by the child until process termination. This is an interpreter proof,
not a skater or a playable backend. Static function and fixture evidence is in
`research/stock-vm-bootstrap-audit.json`; native execution results belong in the
generated report.

The first native `--script` test **passed** in 28 ms, with the child confirmed
stopped at 30 ms (`research/stock-loader-vm-01.jsonl`). The actual original VM
returned `0xd43297cf` (`FALSE`) in one update; original destruction/freeing and
source unloading restored the idle registry and all buckets. This proves the
bounded interpreter/cache lifecycle and still does not provide a playable skater.

All loader modes assign the suspended child to an unnamed Windows Job Object
before creating remote threads. Its noninherited handle has kill-on-close set,
so parent termination also terminates the assigned child. Normal cleanup still
waits for and verifies child termination. `--self-test` checks the 144-byte Win64
job information layout and our small x86 ABI stubs without creating a child.

The separate `probe_animation.py` experiment is selected through the same loader:

```powershell
python -B tools/vanilla_thaw/probe_loader.py "<absolute native directory>/manifest.json" "<absolute new report>.jsonl" --animation "<absolute asset directory>/manifest.json"
```

It validates the private skeleton, permanent animation PAK and two standard-key
tables, then selects one exact original Ollie member by its pinned archive header
and payload hash. Original functions initialize the animation cache, load the
borrowed skeleton and clip, evaluate 52 bones and build their CPU matrix palette.
Four samples cover the start, middle, exact end and repeated middle. The proof
checks finite channels/matrices, changing poses, deterministic repetition and
canaries around aligned host-owned storage. The host writes source inputs and
the original bind translations, but does not decode clips or write output poses.

The original clip destructor and verified cache release run after sampling.
The stock skeleton destructor has not yet been independently mapped, so the
skeleton, its Info and borrowed buffers remain alive until this bounded child
terminates. This mode does not run AnimTree, control a skater, retarget a GTA
character or establish full gameplay parity. Pin scopes and caller evidence are
recorded in `research/stock-animation-bootstrap-audit.json`; six-byte routed-entry
pins are not hashes of complete transitive function bodies.

The first native animation run **passed** in 21 ms; cleanup confirmed its child
stopped at 23 ms (`research/stock-loader-animation-01.jsonl`). Every sample had
52 active bones and finite channels/matrices. The palette changed across time,
and repeated middle samples produced identical channel and palette bytes.
Original clip destruction and cache release succeeded; the primary thread
remained suspended throughout.

Each private `animation_frame` report record also exports `quaternion_bits`,
`translation_bits` and `palette_bits`: exact little-endian IEEE-754 `u32` words
read from the native output, in the original 52-bone order. Their lengths are
208, 208 and 832 words. The existing channel hash covers the quaternion bytes
followed by the translation bytes; the palette hash covers all native matrices.
These are THAW poses for a later adapter, not GTA character matrices. Keep the
generated pose reports private alongside their original game inputs.

The `--native <absolute QB manifest>` mode repeats the existing QB/VM bootstrap,
publishes all original 1,383 CFunctions and 67 member names, and executes one
original `igc_printf` script through the original `IsTrue` callback. Its exact
zero-valued input stays unchanged. Cache binding, complete native symbol graph,
bounded script completion and source cleanup are checked independently; member
methods and arbitrary registered callbacks are not exercised. See
[the native dispatch verifier](probe_native.py) for its checks.

The first complete native dispatch test passed at 43 ms and its child stopped
at 46 ms (`research/stock-loader-native-04.jsonl`). All 1,450 registry symbols
were verified and restored after unloading the script source. Execution evidence
is the original script's verified bindings, known false input and completed
control flow; this is not an independent callback-entry counter.

The `--profile <absolute QB manifest> <absolute profile manifest>` mode also
constructs the original named `skt_info` heap and original ProfileManager. It
loads three complete pinned sources (372,576 bytes), finds the unique
`custom_classic` record among 16 original profiles, and uses native Add/Select
to create and select the actual profile. Original stat getters must initially
return ten values of `3`; ten original setters/getters must then produce
`10/10`. A value of `11` must be rejected without changing the accepted value.

```powershell
python -B tools/vanilla_thaw/probe_profile.py --self-test
python -B tools/vanilla_thaw/probe_loader.py "<absolute native directory>/manifest.json" "<absolute new report>.jsonl" --profile "<absolute QB directory>/manifest.json" "<absolute profile directory>/manifest.json"
```

The native profile test **passed** at 64 ms; cleanup confirmed its child stopped
at 67 ms (`research/stock-loader-profile-01.jsonl`). This includes the preceding
scalar QB, script and native registry regression checks. The genuine profile,
manager, named heap and borrowed source buffers remain child-owned until exit;
their destruction order is not tested. The inline info CStruct, selected Name,
texture pointer, native appearance vtable and stat limits are checked. The
named heap key `0x08682d24` comes from original startup/getter instructions;
it is not derived from its `skt_info` display label. No CSkater, renderer or
gameplay session is created, and this does not establish full trick closure.
