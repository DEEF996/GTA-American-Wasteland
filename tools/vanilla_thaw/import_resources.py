"""Copy fixed stock THAW resource folders into a new private local cache.

Preserves original paths and bytes; never executes game code or changes an
installation. This covers shared resources, not a proven complete asset closure.
"""
import argparse
import os
from pathlib import Path
import shutil
import stat
import tempfile

import import_qb as qb

DIRECTORIES = ("data/models", "data/textures", "data/images", "data/pak",
               "data/anims", "data/worlds/Global", "data/fonts", "data/scripts")
METADATA = "data/streams/bik/mad.bik"
EXTRA_FILES = {
    "data/sounds/pak/global_sfx.pak.wpc": (7591808,
        "c589ebb2049056ffdc87d5ede096cc42e1bd85d2a0cdd422c674573f6388916f"),
}
FORMAT = "gta-thaw-private-stock-resources-v1"


def plain_path(path, missing=False):
    """Reject redirects before resolving them, including Windows junctions."""
    path = Path(path).absolute()
    qb.require(".." not in path.parts, "Resource path traversal is forbidden")
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            qb.require(missing, f"Missing resource path: {part}")
            continue
        qb.require(not stat.S_ISLNK(info.st_mode) and not
                   getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                   f"Symlink or reparse resource path is forbidden: {part}")
    return path


def inventory(source):
    source = plain_path(source).resolve(strict=True)
    names = []
    seen = set()

    def add(path):
        qb.require(path.is_file() and path.resolve(strict=True).is_relative_to(source),
                   f"Missing or escaped resource file: {path}")
        name = path.relative_to(source).as_posix()
        qb.require(name.casefold() not in seen, f"Duplicate resource path: {name}")
        seen.add(name.casefold())
        names.append(name)

    for name in DIRECTORIES:
        directory = plain_path(source / name)
        qb.require(directory.is_dir(), f"Missing stock resource directory: {name}")
        pending = [directory]
        while pending:
            current = pending.pop()
            for child in current.iterdir():
                plain_path(child)
                qb.require(not any(character in child.name for character in ':\\') and
                           child.name == child.name.rstrip(' .'),
                           f"Ambiguous resource filename: {child.name}")
                if child.is_dir():
                    pending.append(child)
                else:
                    add(child)
    add(plain_path(source / METADATA))
    for name in EXTRA_FILES:
        add(plain_path(source / name))
    return sorted(names, key=str.casefold)


def verified_files(source):
    """Inventory and hash every selected resource without rewalking its ancestors."""
    source = plain_path(source).resolve(strict=True)
    rows, directories, seen = [], [], set()

    def inspect(path):
        info = path.lstat()
        qb.require(not stat.S_ISLNK(info.st_mode) and not
                   getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                   f"Symlink or reparse resource path is forbidden: {path}")
        return info

    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def add(path, before):
        qb.require(stat.S_ISREG(before.st_mode), f"Missing or escaped resource file: {path}")
        name = path.relative_to(source).as_posix()
        qb.require(name.casefold() not in seen, f"Duplicate resource path: {name}")
        seen.add(name.casefold())
        digest = qb.setup_sources.digest(path)
        qb.require(identity(before) == identity(inspect(path)),
                   f"Resource changed during verification: {name}")
        rows.append(dict(path=name, bytes=before.st_size, sha256=digest))

    for name in DIRECTORIES:
        directory = plain_path(source / name)
        pending = [(directory, inspect(directory))]
        while pending:
            current, before = pending.pop()
            qb.require(stat.S_ISDIR(before.st_mode), f"Missing stock resource directory: {current}")
            directories.append((current, before))
            with os.scandir(current) as children:
                for child in children:
                    qb.require(not any(character in child.name for character in ':\\') and
                               child.name == child.name.rstrip(' .'),
                               f"Ambiguous resource filename: {child.name}")
                    path = Path(child.path)
                    info = inspect(path)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append((path, info))
                    else:
                        add(path, info)
    for name in (METADATA, *EXTRA_FILES):
        path = plain_path(source / name)
        add(path, inspect(path))
    for directory, before in directories:
        qb.require(identity(before) == identity(inspect(directory)),
                   f"Resource inventory changed during verification: {directory}")
    return sorted(rows, key=lambda row: row["path"].casefold())


def copy_file(source, destination):
    plain_path(source)
    plain_path(destination.parent)
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)


def prepare(configuration, output):
    configuration = plain_path(configuration).resolve(strict=True)
    config, roots, config_bytes = qb.read_selection(configuration)
    for game in roots:
        plain_path(config[game]["directory"])
    source = roots["thaw"]
    output = plain_path(output, missing=True)
    qb.require(not output.exists(), "Refusing to replace an existing resource cache")
    qb.require(all(not output.resolve().is_relative_to(root) for root in roots.values()),
               "Resource cache must stay outside original game directories")
    qb.require(os.name == "nt", "Exclusive atomic resource publication requires Windows")
    for name in qb.setup_sources.THAW_REQUIRED:
        plain_path(source / name)
    declared = config["thaw"]["files"]
    qb.fresh_sources(source, declared)
    names = inventory(source)
    before = qb.setup_sources.files(source, names)
    for row in before:
        if row["path"] in EXTRA_FILES:
            qb.require((row["bytes"], row["sha256"]) == EXTRA_FILES[row["path"]],
                       "Extra stock resource pin differs: " + row["path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = plain_path(output.parent).resolve(strict=True)
    stage = Path(tempfile.mkdtemp(prefix=".stock-resources-", dir=parent))
    expected_stage = stage.resolve(strict=True)
    try:
        for row in before:
            target = stage / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_file(source / row["path"], target)
        qb.require(qb.setup_sources.files(stage, names) == before,
                   "Copied resource bytes differ from source inventory")
        qb.require(inventory(source) == names and qb.setup_sources.files(source, names) == before,
                   "Resource sources changed during import")
        for name in qb.setup_sources.THAW_REQUIRED:
            plain_path(source / name)
        qb.fresh_sources(source, declared)
        qb.require(plain_path(configuration).read_bytes() == config_bytes,
                   "Source configuration changed during resource import")
        manifest = dict(format=FORMAT, source=str(source), directory=str(output),
                        files=before, source_configuration_sha256=qb.sha(config_bytes),
                        sources_revalidated_before_publish=True, original_files_modified=False,
                        backend_ready=False, complete_asset_closure=False)
        with (stage / "manifest.json").open("xb") as stream:
            stream.write(qb.json_bytes(manifest))
        plain_path(output, missing=True)
        qb.require(not output.exists(), "Resource cache destination was created by another process")
        # On Windows this atomic directory rename refuses an existing destination.
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
    print(qb.json_bytes(dict(output=result["directory"], files=len(result["files"]),
                            bytes=sum(row["bytes"] for row in result["files"]),
                            backend_ready=False, complete_asset_closure=False)).decode(), end="")


if __name__ == "__main__":
    main()
