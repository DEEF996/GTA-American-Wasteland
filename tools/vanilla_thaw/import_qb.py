"""Import only the selected stock THAW core QB pair into a private local cache.

No native code executes and no source file is modified. Compiled QB bytes are
preserved without semantic reparsing; this is not a complete runtime asset set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import struct
import sys
import tempfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "gta"))
from assets_export import qbkey, region, require
from carentan_qb import read_member
import setup_sources


FORMAT = "stock-thaw-core-qb-cache-v1"
EXPECTED_MEMBERS = 268
PINNED_INPUTS = {
    "THAW.exe": (21442560, "ff3351529586eefba74d85a807282e18101844e68ecaa5cee20a0a16bed0b118"),
    "data/pak/qb.pak.wpc": (51520, "07af212ce46154d8d59686ece5253759f36e7365cf1ceb203d2475dcd356c2ba"),
    "data/pak/qb.pab.wpc": (2427392, "5ace3109fc05727a28c500bd641f6e91f87aa704d7a6643295f01beb4a224931"),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate configuration key: {key}")
        result[key] = value
    return result


def read_selection(path):
    path = Path(path).resolve(strict=True)
    require(path.stat().st_size <= 1024 * 1024, "Source configuration is too large")
    raw = path.read_bytes()
    config = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)
    require(isinstance(config, dict) and config.get("format") == "gta-thaw-local-sources-v1",
            "Expected configuration written by tools/gta/setup_sources.py")
    roots = {}
    for game in ("gta", "thaw"):
        info = config.get(game)
        require(isinstance(info, dict) and isinstance(info.get("directory"), str), "Missing selected game directory")
        selected = Path(info["directory"])
        require(selected.is_absolute(), "Selected game directories must be absolute")
        roots[game] = selected.resolve(strict=True)
        require(roots[game].is_dir(), "Selected game directory does not exist")
    require(config["thaw"].get("rethawed_present") is False,
            "Configuration does not select a stock-only THAW directory")
    declared = config["thaw"].get("files")
    require(isinstance(declared, list) and len(declared) == len(setup_sources.THAW_REQUIRED),
            "Expected exactly the three selected THAW inputs")
    require(all(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"} for row in declared),
            "Malformed selected THAW file metadata")
    require(tuple(row["path"] for row in declared) == setup_sources.THAW_REQUIRED,
            "Unexpected, duplicate or reordered selected THAW input paths")
    for row in declared:
        expected = PINNED_INPUTS[row["path"]]
        require((row["bytes"], row["sha256"]) == expected,
                f"Unsupported observed stock fingerprint: {row['path']}")
    return config, roots, raw


def fresh_sources(root, declared):
    require(not (root / "reTHAWed.exe").exists() and not (root / "rthw.dll").exists(),
            "Selected THAW directory now contains reTHAWed components")
    actual = setup_sources.files(root, setup_sources.THAW_REQUIRED)
    require(actual == declared, "Selected THAW files changed since source configuration was saved")
    return actual


def canonical_name(raw):
    try:
        name = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Archive member name is not ASCII") from error
    windows = PureWindowsPath(name)
    require(not windows.is_absolute() and not windows.drive and not windows.root,
            "Absolute archive member path is forbidden")
    normalized = name.replace("\\", "/").lower()
    require(all(part not in ("", ".", "..") for part in normalized.split("/")),
            "Archive member traversal or empty path component")
    require(re.fullmatch(r"scripts/(?:[a-z0-9_-]+/)*[a-z0-9_-]+\.qb\.wpc", normalized) is not None,
            f"Unexpected stock QB member name: {name}")
    return name, normalized


def parse_archive(pak, pab):
    """Walk exact archive header order; reuse the existing checked QB lookup."""
    require(32 <= len(pak) <= 8 * 1024 * 1024 and 0 < len(pab) <= 64 * 1024 * 1024,
            "Core QB archive pair exceeds supported bounds")
    combined = pak + pab
    cursor, members, names, checksums, spans = 0, [], set(), set(), []
    while cursor + 32 <= len(pak):
        fields = struct.unpack("<8I", region(pak, cursor, 32, "Core PAK header"))
        kind, relative, size, checksum, slot4, slot5, slot6, flags = fields
        if kind in (0xB524565F, 0x2CB3EF3B):
            require(members, "Core QB archive contains no members")
            padding = pak[cursor+32:]
            require(len(padding) < 192 and set(padding) <= {0, 0xCD},
                    "Unexpected bytes after core PAK terminator")
            terminator_start = cursor + relative
            require(terminator_start >= len(pak), "Core PAK terminator points inside headers")
            terminal = bytes(region(combined, terminator_start, size, "PAK terminator payload"))
            ordered_spans = sorted(spans)
            require(all(a[1] <= b[0] for a, b in zip(ordered_spans, ordered_spans[1:])),
                    "Overlapping core QB payloads")
            require(all(end <= terminator_start or start >= terminator_start+size for start, end in spans),
                    "Core QB payload overlaps archive terminator")
            return members, {"header_offset": cursor, "header_words": list(fields),
                             "payload_offset_combined": terminator_start,
                             "payload_bytes": size, "payload_sha256": sha(terminal),
                             "header_padding_bytes": len(padding), "header_padding_sha256": sha(padding)}
        require(len(members) < 4096, "Excessive core QB member count")
        require(kind == qbkey(".qb") and flags in (0x20, 0x22), "Unexpected core QB entry type or flags")
        name_field = bytes(region(pak, cursor+32, 160, "Core PAK filename"))
        require(b"\0" in name_field, "Unterminated core QB member name")
        original_name, name = canonical_name(name_field.split(b"\0", 1)[0])
        require(name not in names and checksum not in checksums, "Duplicate core QB logical name or filename checksum")
        require(checksum == qbkey(name), "Core QB filename checksum mismatch")
        names.add(name)
        checksums.add(checksum)
        start = cursor + relative
        require(start >= len(pak) and size > 0, "Core QB payload must belong to the selected PAB")
        expected = bytes(region(combined, start, size, "Core QB payload"))
        payload = read_member(combined, name, kind=qbkey(".qb"))
        require(payload == expected, "Existing QB reader disagrees with ordered archive offsets")
        spans.append((start, start+size))
        ordinal = len(members)
        members.append({"order": ordinal, "archive_header_offset": cursor,
                        "archive_header_bytes": 192, "header_words": list(fields),
                        "type_checksum": f"{kind:08x}", "name_checksum": f"{checksum:08x}",
                        "original_name": original_name, "canonical_name": name,
                        "payload_offset_combined": start, "payload_offset_pab": start-len(pak),
                        "bytes": size, "sha256": sha(payload),
                        "cache_file": f"members/{ordinal:04d}.qb", "payload": payload})
        cursor += 192
    raise ValueError("Core PAK terminator is missing")


def remove_staging(stage, expected_stage, parent):
    # Only the exclusively created, still-unredirected staging directory is ours.
    require(stage.resolve() == expected_stage and expected_stage.parent == parent and
            expected_stage != parent and not stage.is_symlink(), "Refusing redirected cache cleanup")
    shutil.rmtree(stage)


def import_cache(configuration, output):
    configuration = Path(configuration).resolve(strict=True)
    config, roots, config_bytes = read_selection(configuration)
    output = Path(output).absolute()
    require(not output.exists(), "Refusing to replace an existing QB cache")
    require(all(not output.resolve().is_relative_to(root) for root in roots.values()),
            "QB cache output must stay outside original game directories")
    require(os.name == "nt", "Atomic exclusive QB cache publication currently requires Windows")
    declared = config["thaw"]["files"]
    actual = fresh_sources(roots["thaw"], declared)
    archive_data = {}
    for row in actual[1:]:
        data = (roots["thaw"] / row["path"]).read_bytes()
        require(len(data) == row["bytes"] and sha(data) == row["sha256"], "QB archive changed during its read")
        archive_data[row["path"]] = data
    members, terminator = parse_archive(archive_data["data/pak/qb.pak.wpc"], archive_data["data/pak/qb.pab.wpc"])
    require(len(members) == EXPECTED_MEMBERS, "Observed stock QB member count differs")
    manifest = {"format": FORMAT, "source_configuration": {"path": str(configuration), "sha256": sha(config_bytes)},
                "source_directory": str(roots["thaw"]), "source_files": actual,
                "source_fingerprint": "pinned observed stock THAW EXE and core QB pair",
                "stock_provenance_verified": False,
                "importer_sha256": sha(Path(__file__).read_bytes()),
                "members": [{k: v for k, v in row.items() if k != "payload"} for row in members],
                "terminator": terminator, "member_count": len(members),
                "payload_bytes": sum(row["bytes"] for row in members),
                "original_files_modified": False, "native_code_executed": False,
                "backend_ready": False, "complete_asset_closure": False,
                "scope": "Only the selected data/pak/qb.pak.wpc and qb.pab.wpc members; no mod archive, animation or audio import."}
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = output.parent.resolve(strict=True)
    # Windows directory rename is atomic and refuses an existing destination.
    stage = Path(tempfile.mkdtemp(prefix=".stock-qb-", dir=parent))
    expected_stage = stage.resolve(strict=True)
    try:
        (stage / "members").mkdir()
        for row in members:
            path = stage / row["cache_file"]
            with path.open("xb") as stream:
                stream.write(row["payload"])
            require(path.stat().st_size == row["bytes"] and setup_sources.digest(path) == row["sha256"],
                    "Written QB member failed checksum verification")
        fresh_sources(roots["thaw"], declared)
        require(configuration.read_bytes() == config_bytes, "Source configuration changed during import")
        manifest["sources_revalidated_before_publish"] = True
        with (stage / "manifest.json").open("xb") as stream:
            stream.write(json_bytes(manifest))
        require(not output.exists(), "QB cache destination was created by another process")
        os.rename(stage, output)
        return manifest
    finally:
        if stage.exists():
            remove_staging(stage, expected_stage, parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True,
                        help="Existing local source configuration from tools/gta/setup_sources.py")
    parser.add_argument("--output", type=Path, required=True, help="New private cache directory outside original installations")
    args = parser.parse_args()
    result = import_cache(args.sources, args.output)
    print(json.dumps({"output": str(args.output.resolve()), "member_count": result["member_count"],
                      "payload_bytes": result["payload_bytes"], "backend_ready": False,
                      "complete_asset_closure": False}))


if __name__ == "__main__":
    main()
