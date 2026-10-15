# Building GTA American Wasteland / Budowanie ze źródeł

[English readme](../README.md) · [Polska instrukcja](../README_PL.md)

Players should use the Windows release ZIP. These instructions are for developers building the integration code. No game content is in this checkout.

Gracze powinni pobrać gotowy ZIP wydania Windows. Poniższa instrukcja dotyczy budowania kodu integracji. Repozytorium nie zawiera zasobów gier.

## Toolchain / Narzędzia

- Windows x64.
- Python 3.12+ x64 with Tk, or the release's private Python runtime.
- Visual Studio C++ Build Tools with x86/x64 MSVC and Windows SDK.
- Stable Rust for `x86_64-pc-windows-msvc`, plus `i686-pc-windows-msvc`.

Run PowerShell from the repository root. Build scripts locate Visual Studio using `vswhere`. The ASI and runtime host are x86; the launcher and Python transport DLL are x64. Their C runtime is linked statically. Toolchains are only developer requirements.

Uruchom PowerShell w głównym folderze repozytorium. Skrypty odnajdują Visual Studio przez `vswhere`. ASI i host silnika są 32-bitowe; launcher i biblioteka transportu Pythona są 64-bitowe. Narzędzia programistyczne nie są wymagane u gracza.

```powershell
rustup target add i686-pc-windows-msvc
cargo fetch --locked --manifest-path tools/gta/runtime_host/Cargo.toml --target i686-pc-windows-msvc
powershell -NoProfile -ExecutionPolicy Bypass -File tools/gta/runtime_host/build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/gta/native/build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/gta/transport_native/build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/gta/launcher_native/build.ps1
```

The build scripts run their native tests and produce binary hash manifests. Packaging checks those binaries and requires the recorded test results. Rebuild after source changes; not every component validates source freshness automatically. The optional `tools/gta/host` Bevy viewer is independent and is not required to play.

Skrypty wykonują testy natywne i zapisują sumy binariów. Pakowanie sprawdza te pliki i wymaga zapisanych wyników testów. Po zmianie źródeł wykonaj nowy build; nie wszystkie komponenty automatycznie sprawdzają aktualność źródeł. Opcjonalny podgląd Bevy w `tools/gta/host` nie jest wymagany do grania.

## Python tests / Testy Pythona

These unit suites do not require live game input. Original-runtime proofs and live gameplay tests require your compatible local games and are separate.

Te testy jednostkowe nie sterują grą. Testy oryginalnych funkcji i rozgrywki wymagają lokalnych zgodnych instalacji i są oddzielne.

```powershell
python -I -B -c "import sys,unittest;sys.path[:0]=['tools/gta','tools','tools/vanilla_thaw'];r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tools/gta'));sys.exit(not r.wasSuccessful())"
python -I -B -c "import sys,unittest;sys.path[:0]=['tools/vanilla_thaw','tools/gta','tools'];r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tools/vanilla_thaw'));sys.exit(not r.wasSuccessful())"
```

## Make distributable packages / Przygotuj paczki

Choose new output directories. The regular package downloads a pinned official Python runtime into a local cache; `--offline` reuses an existing verified archive. Never package your prepared game directory.

Wybierz nowe foldery wynikowe. Zwykła paczka pobiera oficjalny runtime Python o przypiętej sumie; `--offline` korzysta z wcześniej zweryfikowanego archiwum. Nigdy nie pakuj przygotowanej instalacji gry.

```powershell
python -E -s -B tools/gta/package.py --source-only --output dist/Source
python -E -s -B tools/gta/package.py --output dist/Windows
```

The explicit package inventory includes our source, documentation and licenses. Windows additionally includes validated mod builds, the desktop launcher and a private Python/Tk runtime. It excludes original games, extracted resources, private SDKs, research captures, saves and map databases. `PACKAGE_MANIFEST.json` lists shipped files and checksums; verify it before publication.

Jawna lista pakowania obejmuje nasze źródła, dokumentację i licencje. Windows zawiera dodatkowo zweryfikowane buildy moda, launcher oraz prywatny runtime Python/Tk. Wykluczone są gry, wyciągnięte zasoby, prywatne SDK, materiały badawcze, zapisy i bazy map. `PACKAGE_MANIFEST.json` zawiera listę plików i sumy — sprawdź go przed publikacją.

Native addresses and fingerprints are specific to the supported original builds. Porting to another game/version needs a new verified adapter; do not disable those guards to accept unknown binaries.

Adresy funkcji i sumy kontrolne dotyczą konkretnych obsługiwanych wersji. Przeniesienie do innej gry lub wersji wymaga sprawdzonego adaptera; nie wyłączaj kontroli zgodności.
