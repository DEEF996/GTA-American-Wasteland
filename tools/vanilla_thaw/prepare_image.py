"""Copy the pinned native THAW inputs to a private worker directory; never launch them."""
import argparse
import os
from pathlib import Path
import tempfile

import import_qb as qb

FILES = {
    "THAW.exe": (21442560, "ff3351529586eefba74d85a807282e18101844e68ecaa5cee20a0a16bed0b118"),
    "binkw32.dll": (377856, "e3d19e66b6135925116c8e7585b6e6e3746e50d79f1ec863cda615b0189df1a9"),
    "d3dx9_27.dll": (2319568, "a70d571cd675c97c9eeb4a234dba1d667ffb54ec3bb14defb36b3e2f605ae257"),
}


def native_inputs(root):
    rows = qb.setup_sources.files(root, FILES)
    for row in rows:
        qb.require((row["bytes"], row["sha256"]) == FILES[row["path"]],
                   f"Unsupported native input: {row['path']}")
    return rows


def prepare(configuration, output):
    configuration = Path(configuration).resolve(strict=True)
    config, roots, selected_bytes = qb.read_selection(configuration)
    output = Path(output).resolve()
    qb.require(not output.exists(), "Refusing to replace an existing native cache")
    qb.require(all(not output.is_relative_to(root) for root in roots.values()),
               "Native cache must stay outside original game directories")
    qb.require(os.name == "nt", "Exclusive atomic directory publication requires Windows")
    declared = config["thaw"]["files"]
    qb.fresh_sources(roots["thaw"], declared)
    rows = native_inputs(roots["thaw"])
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = output.parent.resolve(strict=True)
    stage = Path(tempfile.mkdtemp(prefix=".stock-image-", dir=parent))
    expected_stage = stage.resolve(strict=True)
    try:
        for row in rows:
            with (roots["thaw"] / row["path"]).open("rb") as stream:
                data = stream.read(row["bytes"] + 1)
            qb.require(len(data) == row["bytes"] and qb.sha(data) == row["sha256"],
                       "Native input changed during copy")
            target = stage / row["path"]
            with target.open("xb") as stream:
                stream.write(data)
            qb.require(qb.setup_sources.digest(target) == row["sha256"],
                       "Native copy failed checksum verification")
        qb.fresh_sources(roots["thaw"], declared)
        qb.require(native_inputs(roots["thaw"]) == rows, "Native inputs changed during preparation")
        qb.require(configuration.read_bytes() == selected_bytes, "Source configuration changed")
        manifest = {
            "format": "gta-thaw-stock-image-probe-v1",
            "source": str(roots["thaw"]), "directory": str(output), "files": rows,
            "source_configuration_sha256": qb.sha(selected_bytes),
            "original_files_modified": False, "entrypoint_allowed": False,
            "backend_ready": False, "sources_revalidated_before_publish": True,
        }
        with (stage / "manifest.json").open("xb") as stream:
            stream.write(qb.json_bytes(manifest))
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
    print(qb.json_bytes({"output": result["directory"], "files": len(result["files"]),
                         "bytes": sum(row["bytes"] for row in result["files"]),
                         "backend_ready": False}).decode(), end="")


if __name__ == "__main__":
    main()
