# Third-party components and locally imported game files

This notice describes the GTA American Wasteland source and integration packages created by
`tools/gta/package.py`. The project's MIT license covers the contributors'
integration code and documentation. It does not grant ownership or distribution
rights to original game code, assets, extracted scripts or locally generated
asset exports. Third-party components retain their own licenses.

GTA San Andreas supplies the target game, CJ and map data. Original PC Tony
Hawk's American Wasteland supplies the compiled gameplay runtime, scripts,
animations, skeleton, board, textures, fonts and sound resources. The current
GTA backend requires the supported stock THAW installation; reTHAWed and Call
of Duty are not runtime dependencies of this package.

Neither GTA package contains game executables, extracted game resources, world
databases or private research captures. The regular integration package includes
our compiled ASI, Rust worker, desktop launcher, build manifests and a private CPython/Tk runtime;
`--source-only` excludes those build outputs and third-party runtime binaries.
The local installer imports the necessary original files from the installations
selected by the user and creates private caches in the isolated GTA copy.

The required `tools/gta/runtime_host` is our 32-bit Rust loader for the supported
local THAW image. It executes original THAW functions without launching the
original game's executable process or entrypoint; the original code is imported
locally, not distributed in either package. This is not a complete Rust rewrite.

The distributed Rust worker also links the Rust standard library and compiler
builtins. Their licenses are separate from our integration code. Notices for
the release toolchain, Rust 1.99.0 (`b940084d7eb6a299eb4bfeb8e34901bc051e7ac4`),
are retained in `tools/gta/licenses/Rust-1.99.0-*`: the complete upstream
standard-library copyright inventory, MIT/Apache and Unicode/BSD terms,
compiler-builtins and libm licenses, and the pinned LLVM compiler-rt license
and contributor credits. The `.html.txt` inventory is the original HTML,
unchanged apart from its filename, and covers more platforms and dependencies
than this Windows binary uses. [Provenance and checksums](tools/gta/licenses/Rust-1.99.0-PROVENANCE.txt)
identify the original files. A rebuild with another Rust toolchain must refresh
these notices before redistributing its binaries.

The optional developer observation tool in `tools/gta/host` is an independent
Rust/Bevy crate. It reads GTA telemetry; the gameplay backend uses original THAW
code rather than a complete Rust rewrite. Cargo resolves that tool's dependencies
separately; vendored dependencies and compiled Cargo outputs are not included.
The direct dependency versions in `tools/gta/host/Cargo.lock` and their declared
upstream licenses are:

| Component | Version | Declared license | Upstream |
| --- | --- | --- | --- |
| Bevy | 0.17.3 | MIT OR Apache-2.0 | https://github.com/bevyengine/bevy |
| serde | 1.0.229 | MIT OR Apache-2.0 | https://github.com/serde-rs/serde |
| serde_json | 1.0.151 | MIT OR Apache-2.0 | https://github.com/serde-rs/json |
| sha2 | 0.10.9 | MIT OR Apache-2.0 | https://github.com/RustCrypto/hashes |
| windows-sys | 0.61.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |

`tools/gta/host/Cargo.lock` records the complete dependency graph for this
crate. Transitive packages retain their own license and notice files; the table
does not replace them. The legacy root Cargo project and isolated reTHAWed
worker are outside the GTA package selection. The native ASI is built with the
separately installed Microsoft compiler and Windows SDK. These tools are needed
to build source packages, not to run the regular integration package.

The regular integration package includes a private official CPython 3.13.16
Windows runtime with Tkinter, copied from a checksum-pinned python.org archive.
It requires no system Python installation. Its upstream license texts are
retained in `runtime/python`, including `LICENSE.txt` and the Tcl/Tk license
files. `runtime/python/PYTHON_RUNTIME.json` records the exact upstream archive,
checksum and runtime inventory. The MIT license of this project does not replace
those licenses. The source-only package contains the staging code and no Python
runtime binaries.

The installer downloads three pinned third-party plugins from their official
release pages, verifies archive hashes and copies the corresponding license
texts into the local installation. Their binaries are not included in either
GTA package. The full upstream MIT notices are retained unchanged here:

| Component | Installer version | Copyright notice | License text |
| --- | --- | --- | --- |
| [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases/tag/v9.7.4) | 9.7.4 | Copyright (c) 2023 ThirteenAG | [MIT](tools/gta/licenses/Ultimate-ASI-Loader-LICENSE.txt) |
| [High FPS Fixes](https://github.com/sonochiwa/sa-high-fps-fixes/releases/tag/v1.9.0) | 1.9.0 | Copyright (c) 2026 Rashid Mamarimov (sonochiwa) | [MIT](tools/gta/licenses/HighFpsFixes-LICENSE.txt) |
| [Windowed Mode](https://github.com/ThirteenAG/III.VC.SA.WindowedMode/releases/tag/v2.2) | 2.2 | Copyright (c) 2017 ThirteenAG | [MIT](tools/gta/licenses/WindowedMode-LICENSE.txt) |

`GTA American Wasteland.exe`, `tools/gta/launcher.py` and `tools/gta/install.py` perform the current local
import. They do not use the legacy private-SDK release workflow. Compatibility
metadata shipped under `tools/vanilla_thaw` contains checksums, offsets and
validation information; the original executable and resource bytes are obtained
from the user's selected installation. Keep the installed games, private
`thaw_gta` caches, research outputs and extracted files outside a public source
release. `PACKAGE_MANIFEST.json` records the files actually selected by the
GTA packager.

This notice documents package contents and license boundaries. It is not a legal
determination about game modification, extraction or compatibility techniques.
