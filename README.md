# GTA American Wasteland

**Skate through San Andreas with the original THAW gameplay engine.**

[Polski / Polish](README_PL.md) · [Build from source](docs/BUILDING.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)

![The bilingual GTA American Wasteland launcher](docs/launcher.png)

An single-player mod for classic **Grand Theft Auto: San Andreas** on Windows. Switch between GTA and skating as CJ, use original Tony Hawk's American Wasteland tricks and animations, build combos, grind eligible world edges, and aim and fire while riding.

**1.0 preview:** a working integration under active testing. Some surfaces, animation transitions and timing spikes still need refinement. This is not a promise of complete THAW parity or compatibility with every GTA mod.

## Play

1. Download the **Windows launcher ZIP** from this repository's Releases page. The automatically generated “Source code” archives are for developers.
2. Extract the entire ZIP to a writable folder outside both games. Keep all its files together.
3. Open **GTA American Wasteland.exe**. Choose **EN** or **PL**.
4. Select your original **GTA San Andreas** folder and your original **Tony Hawk's American Wasteland** folder.
5. Press **Play / Graj**. The launcher remembers your choices, creates a separate GTA copy, prepares the local THAW resources, and starts GTA with the mod.
6. Load a game or start a new one. Keep **Num Lock on**. Wait for the map-preparation status in the upper-right corner, then press **J** to enter THAW mode.

No separate Python, Rust or Visual C++ runtime installation is needed to play. The release contains its own Python/Tk runtime and our compiled components. The first setup needs an internet connection to download three checksum-verified open-source plugins. Initial resource import and map preparation take time; later launches reuse local caches.

The launcher leaves the selected source installations unchanged. Allow space for a separate GTA copy, imported THAW resources and map caches; setup checks free space before copying. Keep the extracted launcher folder and the prepared game folder after setup.

## Supported games

- **Windows x64** and classic GTA San Andreas PC, supported **1.0 US executable**, other executable builds are not supported by this adapter.
- The supported original PC **THAW** build. **reTHAWed is not supported or required.**
- Both games must be supplied by you. Setup checks exact executable/resource fingerprints and rejects incompatible files. It does not download game files or replace an unsupported executable.
- A clean GTA source is recommended. Other ASIs, CLEO scripts, modified maps and custom game binaries are not automatically compatible.

Exact compatibility fingerprints are in [setup_sources.py](tools/gta/setup_sources.py), [prepare_image.py](tools/vanilla_thaw/prepare_image.py) and [import_qb.py](tools/vanilla_thaw/import_qb.py). A matching filename alone does not establish compatibility.

## Default controls

| Input | Action |
| --- | --- |
| **J** | Enter / leave THAW mode |
| **F8** | Settings, bindings and trick assignments |
| **W A S D** | Move / steer |
| **Num5** | Ollie; hold and release to jump |
| **Num4 / Num6 / Num8** | Flip / grab / grind |
| **Num7 / Num9** | Spin left / right |
| **E** | Caveman / mount or dismount, according to state |
| **Space** | Recenter behind CJ while walking; original spine-transfer input while skating |
| **RMB**, then **LMB** | Aim and fire while in THAW mode; LMB alone does not fire |
| **RMB + Shift** | Smooth slow motion while aiming; Shift again exits, maximum five real seconds |
| **Num5 + Num8 + Space** | Grab a nearby suitable vehicle from behind; A/D balance, S releases |

F8 supports keyboard navigation, binding changes and normal/special trick assignments. Skater statistics are set to **10/10**, aerial flips are on by default, and the lower grind camera is off by default. **Infinite grind/manual balance** is optional and off by default; the original perfect-grind mechanism also covers lips, while vehicle-grab balance remains normal. Minimal weapon recoil applies only to shots owned by THAW mode.

The original engine decides which tricks or transfers are available in a given state. A suitable surface and approach are still required. GTA's effects-volume setting also controls THAW sounds.

## What is actually open source?

This repository contains **our integration code**, under the [MIT license](LICENSE): the native GTA adapter, Rust runtime host, importers, gameplay bridge, settings, launcher and tests.

**This is not a complete Rust rewrite of THAW.** Our hidden `gta-thaw-runtime.exe` loads the supported original THAW code from files imported locally and executes the required original functions. It does not start a separate `THAW.exe` or reTHAWed game process. Original animations, board resources, scripts, HUD and sounds also come from your local installation.

Neither the public source nor the downloadable launcher includes GTA/THAW executables, extracted game assets, private map caches or saves. Do not upload your installed game, the generated `thaw_gta` directory, or local research/log folders. Owning a copy does not grant redistribution rights to its contents. This project is not affiliated with or endorsed by either game's rights holders.

## Troubleshooting

| Problem | Check |
| --- | --- |
| Unsupported game/version | Use the exact supported original build; setup deliberately refuses unknown binaries. |
| J does not activate | Load into gameplay, enable Num Lock, focus GTA and read the map status. Close F8 or the pause menu. |
| First launch is slow | Resources and grind caches are being prepared locally. Do not delete those caches between runs. |
| Launcher runtime missing | Extract the entire release ZIP, including `runtime`. Do not use GitHub's source archive to play. |
| Interrupted installation | The incomplete output is kept and never launched. Follow the launcher error and choose a new destination; originals are preserved. |
| A particular edge cannot be ground | Detection is geometric and not every visual edge has suitable collision. Report the location and approach. |

Report the release version, game-build fingerprint and reproducible steps. Review logs before sharing them: they may contain local folder names. See [the technical integration guide](docs/gta-first-run.md) for architecture and bounded verification results.

## Source layout

| Directory | Contents |
| --- | --- |
| `tools/gta/native` | GTA ASI: movement, camera, CJ posing, HUD, audio, weapons and vehicle bridge |
| `tools/gta/runtime_host` | Our Rust x86 host for locally supplied original THAW code |
| `tools/vanilla_thaw` | Version checks, local imports and original-engine integration |
| `tools/gta` | Map/collision preparation, sessions, settings, launcher and packaging |
| `tools/gta/host` | Optional Bevy telemetry viewer for developers |
| `tools/gta/transport_native` | Native shared-memory transport helper |

The source archive is sufficient to build our components; original games are still required for imports and gameplay tests. Dependencies keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
