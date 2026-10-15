"""Import three original profile sources into a new private cache.

Copies exact QB payloads using the existing PAK reader. No game code executes.
"""
import argparse
import os
from pathlib import Path
import tempfile

import import_qb as qb
from import_resources import plain_path

CAS = "data/pak/CAS/cas_wpc.pak.wpc"
CAS_PIN = (338560, "33f4caa290ca2feef20ff69bb3758c78db8a54f84e63d2036931d8e3a7b2ce80")
SOURCES = (
    ("scripts/game/skater/skater_profile.qb.wpc", "0038.qb", 30004,
     "2079462ef4376a217dc2b58a760b0d5db641cf7fdee8cfd7d14b48c27f673aea"),
    ("scripts/game/skater/protricks.qb.wpc", "0042.qb", 4312,
     "dee8327433ddb614db57104014d02f3f80f16090b0666fe7886ed7a1be63a40b"),
    ("pak/cas/cas_wpc_scripts.qb.wpc", "cas_wpc_scripts.qb", 338260,
     "9b2bb670a86eb48609fa635c4c29a34c77273030fe3e860bd55a6a9358e67dd5"),
)


def prepare(configuration, output):
    configuration = plain_path(configuration).resolve(strict=True)
    config, roots, config_bytes = qb.read_selection(configuration)
    source = roots["thaw"]
    output = plain_path(output, missing=True)
    qb.require(not output.exists(), "Refusing to replace an existing profile cache")
    qb.require(all(not output.resolve().is_relative_to(root) for root in roots.values()),
               "Profile cache must stay outside original game directories")
    qb.require(os.name == "nt", "Exclusive atomic publication requires Windows")
    for name in (*qb.setup_sources.THAW_REQUIRED, CAS):
        plain_path(source / name)
    qb.fresh_sources(source, config["thaw"]["files"])
    input_rows = config["thaw"]["files"][1:] + [dict(path=CAS, bytes=CAS_PIN[0], sha256=CAS_PIN[1])]
    archives = {}
    for row in input_rows:
        data = (source / row["path"]).read_bytes()
        qb.require((len(data), qb.sha(data)) == (row["bytes"], row["sha256"]),
                   "Unsupported or changed profile archive: " + row["path"])
        archives[row["path"]] = data
    core = archives["data/pak/qb.pak.wpc"] + archives["data/pak/qb.pab.wpc"]
    payloads = []
    for name, filename, size, digest in SOURCES:
        archive = archives[CAS] if name.startswith("pak/cas/") else core
        # CAS stores the unqualified .qb filename in this platform archive.
        lookup = name[:-4] if name.startswith("pak/cas/") else name
        kind = 0x5d796624 if name.startswith("pak/cas/") else qb.qbkey(".qb")
        data = qb.read_member(archive, lookup, kind=kind)
        qb.require((len(data), qb.sha(data)) == (size, digest), "Original profile payload differs: " + name)
        payloads.append((name, filename, data))
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = plain_path(output.parent).resolve(strict=True)
    stage = Path(tempfile.mkdtemp(prefix=".stock-profile-", dir=parent))
    expected_stage = stage.resolve(strict=True)
    try:
        rows = []
        for name, filename, data in payloads:
            with (stage / filename).open("xb") as stream:
                stream.write(data)
            qb.require((stage / filename).read_bytes() == data, "Private profile copy differs")
            rows.append(dict(canonical_name=name, path=str(output / filename), bytes=len(data), sha256=qb.sha(data)))
        for row in input_rows:
            path = plain_path(source / row["path"])
            qb.require(path.read_bytes() == archives[row["path"]], "Profile source changed during import")
        qb.fresh_sources(source, config["thaw"]["files"])
        qb.require(plain_path(configuration).read_bytes() == config_bytes, "Source configuration changed")
        manifest = dict(format="gta-thaw-private-stock-profile-v1", source=str(source), directory=str(output),
                        source_configuration_sha256=qb.sha(config_bytes), sources=rows, input_archives=input_rows,
                        sources_revalidated_before_publish=True, original_files_modified=False,
                        game_material_private=True, backend_ready=False, complete_asset_closure=False)
        (stage / "manifest.json").write_bytes(qb.json_bytes(manifest))
        plain_path(output, missing=True)
        qb.require(not output.exists(), "Profile destination was created during import")
        os.rename(stage, output)
        return manifest
    finally:
        if stage.exists():
            plain_path(stage)
            qb.remove_staging(stage, expected_stage, parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.sources, args.output)
    print(qb.json_bytes(dict(output=result["directory"], sources=len(result["sources"]),
                            bytes=sum(row["bytes"] for row in result["sources"]), backend_ready=False)).decode(), end="")


if __name__ == "__main__":
    main()
