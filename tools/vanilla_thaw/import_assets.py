"""Import the supported stock skeleton, board and animation inputs locally.

Uses the existing asset exporters. The cache contains private game material,
not a complete gameplay asset set or redistributable content.
"""
import argparse
import os
from pathlib import Path
import shutil
import tempfile

import import_qb as qb
import assets_models
import assets_skeleton

COPIES = ("data/pak/perm_anims/perm_anims.pak.wpc",
          "data/pak/unloadable_anims/unloadable_anims.pak.wpc",
          "data/anims/standardkeyQ.bin", "data/anims/standardkeyT.bin")
MODEL = "data/models/Skater_Male/board_default.skin.wpc"
TEXTURE = "data/models/Skater_Male/board_default.tex.wpc"
GLOBAL = "data/worlds/Global/global.pak.wpc"
INPUTS = {
    COPIES[0]: (1342848, "8e78795dcbd562093949685162d8584185adaaa76b0f5451fcdc02578feb1fd2"),
    COPIES[1]: (2495264, "615eb8722a94af809bd1ae9b6b7b74833f967c7f1275d5dbc20f84adaca2769f"),
    COPIES[2]: (2048, "07c5975374246679dd72c8cd1162c00ab01ef93c20342dbb385f710f5acdd876"),
    COPIES[3]: (2048, "504c8d229389c48841e2013dddc586765652fe65191ee53a99908f9b07b33548"),
    MODEL: (28244, "6ad165f95bbd50917d1b73a99e765cfbc74a149c40a2dd785739af7777f2f21f"),
    TEXTURE: (18728, "f1d833852e46bcb29bf8ed4277273f9656e86745dcf9a716fe633d268e811da8"),
    GLOBAL: (10496, "e78e4147a73a59c32accce5de32fa20d5ea32f3860c361010920a30d6d13923a"),
    "data/worlds/Global/global.pab.wpc": (2672672, "cefddab597f85f1a7353a2ae0adbf179f26568d091f373bd4c2ed359a6612cc8"),
    "data/pak/dbg.pak.wpc": (13060640, "846e58ec9203e36c49e3a3a673984cc4839644cc5f2e739d1c6893235455a407"),
}


def asset_inputs(source):
    rows = qb.setup_sources.files(source, INPUTS)
    for row in rows:
        qb.require((row["bytes"], row["sha256"]) == INPUTS[row["path"]],
                   f"Unsupported stock asset: {row['path']}")
    return rows


def prepare(configuration, output):
    configuration = Path(configuration).resolve(strict=True)
    config, roots, config_bytes = qb.read_selection(configuration)
    source = roots["thaw"]
    output = Path(output).resolve()
    qb.require(not output.exists(), "Refusing to replace an existing asset cache")
    qb.require(all(not output.is_relative_to(root) for root in roots.values()),
               "Asset cache must stay outside original game directories")
    qb.require(os.name == "nt", "Exclusive atomic directory publication requires Windows")
    qb.fresh_sources(source, config["thaw"]["files"])
    before = asset_inputs(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = output.parent.resolve(strict=True)
    stage = Path(tempfile.mkdtemp(prefix=".stock-assets-", dir=parent))
    expected_stage = stage.resolve(strict=True)
    try:
        rig = assets_skeleton.export(source, stage / "rig_thps7_human.json")
        board = assets_models.export(source / MODEL, source / TEXTURE, stage / "board_default")
        qb.require(rig["bone_count"] == 52 and board["vertices"] == 558 and board["triangles"] == 552
                   and board["textures"] == 5 and board["weighted_vertices"] == 558
                   and not board["unresolved_texture_checksums"], "Unexpected stock skeleton or board")
        rig["output"], board["output"] = str(output / "rig_thps7_human.json"), str(output / "board_default")
        payload, origin = assets_skeleton.extract_entry(source / GLOBAL, 0x39937D56)
        qb.require(len(payload) == 5376, "Unexpected native skeleton size")
        (stage / "thps7_human.ske").write_bytes(payload)
        for name in COPIES:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            row = next(row for row in before if row["path"] == name)
            qb.require(target.stat().st_size == row["bytes"] and qb.setup_sources.digest(target) == row["sha256"],
                       "Animation input changed during copy")
        generated = qb.setup_sources.files(stage, sorted(
            path.relative_to(stage).as_posix() for path in stage.rglob("*") if path.is_file()))
        qb.require(asset_inputs(source) == before, "Asset inputs changed during export")
        qb.fresh_sources(source, config["thaw"]["files"])
        qb.require(configuration.read_bytes() == config_bytes, "Source configuration changed")
        manifest = dict(format="gta-thaw-private-stock-assets-v1", source=str(source), directory=str(output),
                        source_configuration_sha256=qb.sha(config_bytes), inputs=before, files=generated,
                        rig=rig, board=board,
                        native_skeleton=dict(path="thps7_human.ske", bytes=len(payload), sha256=qb.sha(payload), source=origin),
                        original_files_modified=False, sources_revalidated_before_publish=True,
                        game_material_private=True, complete_asset_closure=False,
                        gta_retargeting_verified=False, backend_ready=False)
        (stage / "manifest.json").write_bytes(qb.json_bytes(manifest))
        os.rename(stage, output)
        return manifest
    finally:
        if stage.exists():
            qb.remove_staging(stage, expected_stage, parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.sources, args.output)
    print(qb.json_bytes(dict(output=result["directory"], files=len(result["files"]),
                            bytes=sum(row["bytes"] for row in result["files"]),
                            bones=result["rig"]["bone_count"], backend_ready=False)).decode(), end="")


if __name__ == "__main__":
    main()
