"""Local GTA SA world collision import; no game process or source files are changed.

The database keeps mesh, box and sphere collision separately, instances in GTA
Z-up coordinates, packed IPL flags, interiors, time windows and optional IPLs.
It is a static placement catalogue, NOT a replacement for live GTA streaming.

Format references (no third-party implementation code is vendored):
https://github.com/gta-reversed/gta-reversed/blob/master/source/game_sa/Collision/ColHelpers.h
https://github.com/gta-reversed/gta-reversed/blob/master/source/game_sa/FileLoader.cpp
https://github.com/gta-reversed/gta-reversed/blob/master/source/game_sa/FileObjectInstance.h
https://github.com/gta-reversed/gta-reversed/blob/master/source/game_sa/Entity/Entity.h
https://github.com/Parik27/DragonFF/blob/master/gtaLib/img.py
https://github.com/Parik27/DragonFF/blob/master/gtaLib/map.py
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import itertools
import json
import math
from pathlib import Path, PureWindowsPath
import sqlite3
import struct
import zlib


SCHEMA = "gta-sa-world-collision-v1"
SECTOR_SIZE = 128.0
MAX_MEMBER = 64 * 1024 * 1024
# Stock THAW movement arguments use inches/second; GTA's raw velocity uses
# metres per normalized 1/50-second step. See docs/stock-skater-path.md.
THAW_UNITS_PER_GTA_UNIT = 1.0 / .0254
GTA_STEPS_PER_SECOND = 50.0


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True,
                      allow_nan=False).encode("utf-8")


def finite(values):
    require(all(math.isfinite(v) and abs(v) <= 10_000_000 for v in values),
            "Non-finite or out-of-range geometry coordinate")


def checked_box(lower, upper):
    finite((*lower, *upper))
    require(all(a <= b for a, b in zip(lower, upper)), "Inverted collision bounds")


def unpack(data, fmt, offset=0):
    require(0 <= offset <= len(data) - struct.calcsize(fmt),
            f"Truncated {fmt} record at {offset}")
    return struct.unpack_from(fmt, data, offset)


def name_of(raw):
    name = raw.split(b"\0", 1)[0].decode("ascii").lower()
    require(name and not any(c in name for c in "/\\:"), "Invalid archive/model name")
    return name


def local_path(root, relative):
    relative = PureWindowsPath(relative)
    require(not relative.drive and not relative.root and
            all(p not in ("..", ".") for p in relative.parts), "Unsafe game-relative path")
    result = root.joinpath(*relative.parts).resolve()
    require(result.is_relative_to(root.resolve()), "Game-relative path escapes installation")
    return result


@dataclass(frozen=True)
class ImgEntry:
    name: str
    offset: int
    size: int


def img_directory(path):
    """Read SA VER2 index; never extract member paths into the filesystem."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        magic, count = unpack(stream.read(8), "<4sI")
        require(magic == b"VER2", f"Only SA VER2 IMG is supported: {path.name}")
        require(count <= 1_000_000 and 8 + count * 32 <= size, "Invalid IMG directory size")
        raw = stream.read(count * 32)
    entries, names = [], set()
    for i in range(count):
        sector, sectors, raw_name = unpack(raw, "<II24s", i * 32)
        name = name_of(raw_name)
        offset, length = sector * 2048, sectors * 2048
        require(name not in names, f"Duplicate IMG member: {name}")
        require(offset >= 8 + count * 32 and length > 0 and offset + length <= size,
                f"IMG member outside archive: {name}")
        names.add(name)
        entries.append(ImgEntry(name, offset, length))
    return entries


def member_bytes(stream, entry):
    require(entry.size <= MAX_MEMBER, f"Oversize collision/IPL member: {entry.name}")
    stream.seek(entry.offset)
    data = stream.read(entry.size)
    require(len(data) == entry.size, f"Short IMG read: {entry.name}")
    return data


def parse_col(data):
    """Yield COLL/COL2/COL3 models, excluding render-only shadow triangles."""
    cursor = 0
    while cursor < len(data):
        if not any(data[cursor:cursor + 4]):
            require(not any(data[cursor:]), "Non-zero bytes after COL padding")
            break
        magic, size, raw_name, model_id = unpack(data, "<4sI22sH", cursor)
        require(magic in (b"COLL", b"COL2", b"COL3"), f"Unsupported collision format {magic!r}")
        end = cursor + size + 8
        require(size >= 64 and end <= len(data), "COL model size exceeds member")
        raw = memoryview(data)[cursor:end]
        name = name_of(raw_name)
        version = 1 if magic == b"COLL" else int(chr(magic[3]))
        values = unpack(raw, "<10f", 32)
        lower, upper = (values[4:7], values[7:10]) if version == 1 else (values[:3], values[3:6])
        checked_box(lower, upper)
        spheres, boxes, faces, vertices = [], [], [], []
        flags = 2
        if version == 1:
            position = 72

            def legacy_records(fmt):
                nonlocal position
                count, = unpack(raw, "<I", position)
                position += 4
                require(count <= 1_000_000, "Excessive legacy COL record count")
                total = count * struct.calcsize(fmt)
                require(position + total <= len(raw), "Legacy COL records exceed model")
                result = list(struct.iter_unpack(fmt, raw[position:position + total]))
                position += total
                return result

            for radius, x, y, z, material, flag, brightness, light in legacy_records("<4f4B"):
                spheres.append([x, y, z, radius, material, flag, brightness, light])
            lines, = unpack(raw, "<I", position)
            position += 4
            require(lines == 0, "Legacy COL lines need explicit support")
            boxes = [list(row) for row in legacy_records("<6f4B")]
            vertices = [list(row) for row in legacy_records("<3f")]
            faces = [[a, b, c, mat, light] for a, b, c, mat, flag, bright, light
                     in legacy_records("<3I4B")]
        else:
            header_size = 108 if version == 2 else 120
            require(len(raw) >= header_size, "Truncated COL2/3 header")
            ns, nb, nf, nl, flags, os, ob, ol, ov, of, op = unpack(raw, "<HHHBx7I", 72)
            require(nl == 0, "COL line/cone primitives need explicit support")

            def records(fmt, offset, count):
                if count == 0:
                    return []
                offset += 4  # COL2/3 offsets are measured from the size field.
                length = count * struct.calcsize(fmt)
                require(header_size <= offset <= len(raw) - length,
                        f"COL {name} array outside its model")
                return [list(row) for row in struct.iter_unpack(fmt, raw[offset:offset + length])]

            spheres = records("<4f4B", os, ns)
            boxes = records("<6f4B", ob, nb)
            faces = records("<3H2B", of, nf)
            nv = 1 + max((max(row[:3]) for row in faces), default=-1)
            vertices = [[v / 128.0 for v in row] for row in records("<3h", ov, nv)]
            if version == 3:
                shadow_faces, shadow_vertices_offset, shadow_faces_offset = unpack(raw, "<3I", 108)
                require(shadow_faces <= 1_000_000, "Excessive shadow face count")
                # Shadow mesh is deliberately not used for gameplay collision.
        for sphere in spheres:
            finite(sphere[:4])
            require(sphere[3] >= 0, "Negative sphere radius")
        for box in boxes:
            checked_box(box[:3], box[3:6])
        for vertex in vertices:
            finite(vertex)
        require(all(max(f[:3]) < len(vertices) for f in faces), "COL face references missing vertex")
        yield {"name": name, "model_id_hint": model_id, "version": version,
               "bounds": [lower, upper], "flags": flags, "vertices": vertices,
               "faces": faces, "boxes": boxes, "spheres": spheres}
        cursor = end


def text_sections(text):
    section = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue
        if line.lower() == "end":
            section = None
            continue
        fields = line.replace(",", " ").split()
        if section is None:
            require(len(fields) == 1, f"Missing section header at line {number}")
            section = fields[0].lower()
        else:
            yield section, number, fields


def parse_ide(text):
    for section, number, row in text_sections(text):
        if section not in ("objs", "tobj", "anim"):
            continue
        require(len(row) >= (7 if section == "tobj" else 5), f"Invalid IDE line {number}")
        flags = int(row[-3] if section == "tobj" else row[-1], 0)
        time_window = [int(row[-2]), int(row[-1])] if section == "tobj" else None
        if time_window:
            require(all(0 <= value <= 24 for value in time_window), "Invalid IDE time window")
        model_id = int(row[0])
        require(0 <= model_id <= 65535, "Invalid IDE model id")
        yield {"id": model_id, "name": row[1].lower(), "kind": section,
               "flags": flags, "time_window": time_window}


def placement(model_id, position, quaternion, packed, lod, name=""):
    finite((*position, *quaternion))
    require(0 <= model_id <= 65535 and -1 <= lod <= 1_000_000, "Invalid IPL model/LOD id")
    require(0 <= packed <= 0xffffffff, "Invalid IPL packed instance flags")
    require(0.98 <= sum(v*v for v in quaternion) <= 1.02, "Non-unit IPL quaternion")
    return {"model_id": model_id, "name": name.lower(), "position": list(position),
            "quaternion": list(quaternion), "instance_flags": packed,
            "interior": packed & 255, "lod": lod}


def parse_ipl(data):
    if data[:4] == b"bnry":
        require(len(data) >= 76, "Truncated binary IPL header")
        count, = unpack(data, "<I", 4)
        offset, = unpack(data, "<I", 28)
        require(count <= 1_000_000 and offset >= 76 and offset + count * 40 <= len(data),
                "Binary IPL instance array outside member")
        for i in range(count):
            row = unpack(data, "<7fiIi", offset + i * 40)
            yield placement(row[7], row[:3], row[3:7], row[8], row[9])
    else:
        for section, number, row in text_sections(data.decode("latin-1")):
            if section == "inst":
                require(len(row) == 11, f"Invalid SA text IPL instance at line {number}")
                yield placement(int(row[0]), [float(v) for v in row[3:6]],
                                [float(v) for v in row[6:10]], int(row[2]), int(row[10]), row[1])


def rotation_matrix(quaternion, packed=0):
    """Match SA's IPL heading fast path and conjugated general quaternion."""
    x, y, z, w = quaternion
    if abs(x) <= .05 and abs(y) <= .05 and not (packed & 0x200 and x != 0 and y != 0):
        angle = math.acos(max(-1.0, min(1.0, w))) * (2 if z < 0 else -2)
        c, s = math.cos(angle), math.sin(angle)
        return ((c, -s, 0), (s, c, 0), (0, 0, 1))
    x, y, z = -x, -y, -z
    return ((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
            (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
            (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)))


def transform(point, matrix, translation):
    return [sum(row[i] * point[i] for i in range(3)) + translation[n]
            for n, row in enumerate(matrix)]


def world_bounds(model_bounds, instance):
    matrix = rotation_matrix(instance["quaternion"], instance["instance_flags"])
    corners = [transform(p, matrix, instance["position"])
               for p in itertools.product(*zip(*model_bounds))]
    return [[min(p[a] for p in corners) for a in range(3)],
            [max(p[a] for p in corners) for a in range(3)]]


def source_horizontal_forward(matrix):
    """THAW yaw reference shared by physical movement and model presentation.

    A vertical forward axis is valid during wall/parkour movement. Its right
    axis still defines yaw; never release gameplay just for that orientation.
    """
    require(len(matrix) == 16 and all(math.isfinite(v) for v in matrix),
            'A finite original actor matrix is required')
    rows = [matrix[i:i+3] for i in (0, 4, 8)]
    dot = lambda a, b: sum(x*y for x, y in zip(a, b))
    cross = [rows[0][1]*rows[1][2]-rows[0][2]*rows[1][1],
             rows[0][2]*rows[1][0]-rows[0][0]*rows[1][2],
             rows[0][0]*rows[1][1]-rows[0][1]*rows[1][0]]
    require(all(abs(dot(row, row)-1) < .01 for row in rows) and
            all(abs(dot(rows[a], rows[b])) < .01 for a, b in ((0,1), (0,2), (1,2))) and
            dot(cross, rows[2]) > .99, 'Original actor axes are not a rigid right-handed basis')
    fx, fz = matrix[8], matrix[10]
    length = math.hypot(fx, fz)
    if length <= .1:
        fx, fz = -matrix[2], matrix[0]
        length = math.hypot(fx, fz)
    require(length > .1, 'Original actor axes do not define a horizontal heading')
    return fx/length, fz/length


def gta_to_thaw(point, scale=THAW_UNITS_PER_GTA_UNIT, origin=(0, 0, 0)):
    """World coordinates only; actor-to-feet offsets are a separate calibration."""
    require(math.isfinite(scale) and scale > 0, "Explicit positive GTA-to-THAW scale required")
    require(len(point) == len(origin) == 3, "Expected three-dimensional coordinates")
    finite((*point, *origin))
    x, y, z = [point[i] - origin[i] for i in range(3)]
    result = [x * scale, z * scale, -y * scale]
    finite(result)
    return result


def thaw_to_gta(point, scale=THAW_UNITS_PER_GTA_UNIT, origin=(0, 0, 0)):
    require(math.isfinite(scale) and scale > 0, "Explicit positive GTA-to-THAW scale required")
    require(len(point) == len(origin) == 3, "Expected three-dimensional coordinates")
    finite((*point, *origin))
    x, y, z = point
    result = [origin[0] + x / scale, origin[1] - z / scale, origin[2] + y / scale]
    finite(result)
    return result


def gta_velocity_to_thaw(raw_velocity):
    """Inches/second, independent of render FPS, last timestep and world origin."""
    return gta_to_thaw(raw_velocity, THAW_UNITS_PER_GTA_UNIT * GTA_STEPS_PER_SECOND)


def thaw_velocity_to_gta(velocity):
    return thaw_to_gta(velocity, THAW_UNITS_PER_GTA_UNIT * GTA_STEPS_PER_SECOND)


def data_references(root):
    refs = {"IDE": [], "IPL": [], "COLFILE": [], "IMG": []}
    for name in ("default.dat", "gta.dat"):
        for raw in (root / "data" / name).read_text(encoding="latin-1").splitlines():
            row = raw.split("#", 1)[0].strip().split()
            if row and row[0].upper() in refs:
                path = local_path(root, row[-1])
                require(path.is_file(), f"Missing declared GTA file: {row[-1]}")
                refs[row[0].upper()].append(path)
    return refs


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_world(root, output, sector_size=SECTOR_SIZE):
    """Create a fresh local database. Failed imports remain clearly incomplete."""
    root, output = Path(root).resolve(), Path(output).absolute()
    require(root.is_dir(), "GTA installation directory does not exist")
    require(not output.exists(), "Refusing to overwrite an existing world database")
    require(not output.resolve().is_relative_to(root), "Generated database must be outside source installation")
    require(math.isfinite(sector_size) and 16 <= sector_size <= 1024, "Invalid sector size")
    refs = data_references(root)
    archives = list(dict.fromkeys([root / "models/gta3.img", root / "models/gta_int.img", *refs["IMG"]]))
    directories = {p: img_directory(p) for p in archives}
    dynamic_file = root / "data/object.dat"
    dynamic_names = {line.split()[0].lower() for raw in dynamic_file.read_text(encoding="latin-1").splitlines()
                     if (line := raw.split(";", 1)[0].split("#", 1)[0].strip()) and line != "*"}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also closes a race between the exists check and SQLite.
    with output.open("xb"):
        pass
    db = sqlite3.connect(output)
    stats = Counter()
    inputs = list(dict.fromkeys([root / "data/default.dat", root / "data/gta.dat", dynamic_file,
                                *refs["IDE"], *refs["IPL"], *refs["COLFILE"], *archives]))
    try:
        db.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE models (name TEXT PRIMARY KEY, payload BLOB NOT NULL, sha256 TEXT NOT NULL,
                                 source TEXT NOT NULL);
            CREATE TABLE definitions (id INTEGER PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE instances (id INTEGER PRIMARY KEY, name TEXT NOT NULL, area INTEGER NOT NULL,
                stream TEXT NOT NULL, conditional INTEGER NOT NULL, dynamic INTEGER NOT NULL,
                has_collision INTEGER NOT NULL, minx REAL, miny REAL, minz REAL,
                maxx REAL, maxy REAL, maxz REAL, payload TEXT NOT NULL);
            CREATE TABLE sectors (area INTEGER, x INTEGER, y INTEGER, instance_id INTEGER,
                                  PRIMARY KEY (area,x,y,instance_id));
            CREATE INDEX sector_instances ON sectors(instance_id);
        """)
        db.execute("INSERT INTO metadata VALUES (?,?)", ("schema", json.dumps(SCHEMA)))
        db.execute("INSERT INTO metadata VALUES (?,?)", ("complete", "false"))
        db.commit()
        definitions = {}
        for path in refs["IDE"]:
            for definition in parse_ide(path.read_text(encoding="latin-1")):
                old = definitions.get(definition["id"])
                require(old is None or old == definition, f"Conflicting IDE id: {definition['id']}")
                definitions[definition["id"]] = definition
        db.executemany("INSERT INTO definitions VALUES (?,?,?)",
                       [(key, value["name"], encoded(value).decode()) for key, value in definitions.items()])
        models = {}

        def add_collision(data, source):
            stats["collision_files"] += 1
            for model in parse_col(data):
                payload = encoded(model)
                digest = hashlib.sha256(payload).hexdigest()
                name = model["name"]
                if name in models:
                    require(models[name]["sha256"] == digest, f"Conflicting collision model: {name}")
                    stats["identical_collision_duplicates"] += 1
                    continue
                models[name] = {"bounds": model["bounds"], "sha256": digest,
                                "enabled": bool(model["flags"] & 2),
                                "primitives": len(model["faces"]) + len(model["boxes"]) + len(model["spheres"])}
                db.execute("INSERT INTO models VALUES (?,?,?,?)", (name, zlib.compress(payload), digest, source))
                stats["collision_models"] += 1
                stats[f"col{model['version']}_models"] += 1
                for key in ("faces", "vertices", "boxes", "spheres"):
                    stats[key] += len(model[key])

        for path in refs["COLFILE"]:
            require(path.stat().st_size <= MAX_MEMBER, "Oversize loose COL")
            add_collision(path.read_bytes(), path.relative_to(root).as_posix())
        for path, entries in directories.items():
            with path.open("rb") as stream:
                for entry in entries:
                    if entry.name.endswith(".col"):
                        add_collision(member_bytes(stream, entry), f"{path.relative_to(root).as_posix()}::{entry.name}")

        normal_streams = {p.stem.lower() for p in refs["IPL"]}
        missing = Counter()
        areas = Counter()
        optional = Counter()

        def add_instances(data, source, stream_name, conditional):
            stats["binary_ipl_files" if data[:4] == b"bnry" else "text_ipl_files"] += 1
            for instance in parse_ipl(data):
                definition = definitions.get(instance["model_id"])
                require(definition is not None, f"IPL references unknown IDE {instance['model_id']} in {source}")
                name = definition["name"]
                require(not instance["name"] or instance["name"] == name, f"IPL/IDE name mismatch: {name}")
                instance.update(name=name, source=source, definition=definition,
                                stream=stream_name, conditional=bool(conditional),
                                dynamic=(name in dynamic_names or definition["kind"] == "anim"))
                model = models.get(name)
                has_collision = bool(model and model["enabled"] and model["primitives"])
                bounds = world_bounds(model["bounds"], instance) if model else [instance["position"]] * 2
                instance["world_bounds"] = bounds
                lo, hi = bounds
                cur = db.execute("INSERT INTO instances(name,area,stream,conditional,dynamic,has_collision,minx,miny,minz,maxx,maxy,maxz,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (name, instance["interior"], stream_name, int(conditional), int(instance["dynamic"]),
                                  int(has_collision), *lo, *hi, encoded(instance).decode()))
                sectors = [(math.floor(lo[i] / sector_size), math.floor(hi[i] / sector_size)) for i in (0, 1)]
                require(math.prod(b-a+1 for a, b in sectors) <= 16384, f"Excessive sector coverage: {name}")
                db.executemany("INSERT INTO sectors VALUES (?,?,?,?)",
                               ((instance["interior"], x, y, cur.lastrowid)
                                for x in range(sectors[0][0], sectors[0][1]+1)
                                for y in range(sectors[1][0], sectors[1][1]+1)))
                stats["instances"] += 1
                stats["collidable_instances"] += int(has_collision)
                stats["dynamic_instances"] += int(instance["dynamic"])
                if not model:
                    missing[name] += 1
                if conditional:
                    optional[stream_name] += 1
                areas[instance["interior"]] += 1

        for path in refs["IPL"]:
            add_instances(path.read_bytes(), path.relative_to(root).as_posix(), path.stem.lower(), False)
        for path, entries in directories.items():
            with path.open("rb") as stream:
                for entry in entries:
                    if entry.name.endswith(".ipl"):
                        stem = Path(entry.name).stem
                        parent = stem.rsplit("_stream", 1)[0]
                        conditional = stem not in normal_streams and parent not in normal_streams
                        add_instances(member_bytes(stream, entry),
                                      f"{path.relative_to(root).as_posix()}::{entry.name}", stem, conditional)
        report = {"schema": SCHEMA, "coordinate_system": "gta-z-up",
                  "sector_size": sector_size, "stats": dict(stats),
                  "interiors": dict(sorted(areas.items())), "conditional_streams": dict(sorted(optional.items())),
                  "instances_without_col": dict(sorted(missing.items())),
                  "source_files": [{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size,
                                    "sha256": file_digest(p)} for p in inputs],
                  "limitations": ["Static file placements only; live dynamic entity transforms and disabled entities need the GTA adapter.",
                                  "Script-controlled IPLs are excluded until explicitly activated.",
                                  "Model instances without COL are recorded, not replaced by render geometry.",
                                  "Sphere primitives remain analytic; triangle-only export must report its approximation.",
                                  "Water and procedurally/script-created geometry are not authored by IPL collision import."]}
        db.execute("INSERT INTO metadata VALUES (?,?)", ("report", encoded(report).decode()))
        db.execute("UPDATE metadata SET value='true' WHERE key='complete'")
        db.commit()
        require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity check failed")
        return report
    finally:
        db.close()


def active_hour(window, hour):
    if window is None:
        return True
    start, end = window
    if start == end:
        return True
    return start <= hour < end if start < end else hour >= start or hour < end


def query_world(database, center, radius, interior=0, active_streams=(), hour=12,
                include_dynamic=False, max_instances=10000, max_primitives=250000):
    """Bounded sector query; fail rather than silently truncating native input."""
    finite(center)
    require(len(center) == 3 and math.isfinite(radius) and 0 < radius <= 2048, "Invalid query region")
    require(type(interior) is int and 0 <= interior <= 255, "Invalid interior")
    require(0 <= hour < 24 and 0 < max_instances <= 100000 and 0 < max_primitives <= 2000000,
            "Invalid query budget/time")
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        metadata = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM metadata")}
        require(metadata.get("schema") == SCHEMA and metadata.get("complete") is True, "Incomplete or unsupported geometry database")
        report = metadata["report"]
        size = report["sector_size"]
        low, high = [[v + s * radius for v in center] for s in (-1, 1)]
        # CEntity::IsInArea treats IPL area 13 as present in every current area.
        rows = db.execute("""SELECT DISTINCT i.id,i.payload,i.has_collision FROM sectors s JOIN instances i ON i.id=s.instance_id
            WHERE s.area IN (?,13) AND s.x BETWEEN ? AND ? AND s.y BETWEEN ? AND ?
            AND i.minx<=? AND i.maxx>=? AND i.miny<=? AND i.maxy>=? AND i.minz<=? AND i.maxz>=?
            ORDER BY i.id LIMIT ?""", (interior, math.floor(low[0]/size), math.floor(high[0]/size),
                                      math.floor(low[1]/size), math.floor(high[1]/size),
                                      high[0], low[0], high[1], low[1], high[2], low[2], max_instances+1)).fetchall()
        require(len(rows) <= max_instances, "Region exceeds instance budget; request a smaller region")
        selected, models, omitted = [], {}, Counter()
        primitives = 0
        active = set(active_streams)
        for instance_id, payload, has_collision in rows:
            instance = json.loads(payload)
            if instance["conditional"] and instance["stream"] not in active:
                omitted["inactive_conditional"] += 1
                continue
            if instance["dynamic"] and not include_dynamic:
                omitted["requires_live_dynamic_transform"] += 1
                continue
            if not active_hour(instance["definition"]["time_window"], hour):
                omitted["inactive_time_model"] += 1
                continue
            if not has_collision:
                omitted["without_collision"] += 1
                continue
            name = instance["name"]
            if name not in models:
                compressed, = db.execute("SELECT payload FROM models WHERE name=?", (name,)).fetchone()
                model_data = zlib.decompress(compressed)
                require(len(model_data) <= MAX_MEMBER, "Oversize stored model")
                models[name] = json.loads(model_data)
            model = models[name]
            primitives += len(model["faces"]) + len(model["boxes"]) + len(model["spheres"])
            require(primitives <= max_primitives, "Region exceeds primitive budget; request a smaller region")
            instance["instance_id"] = instance_id
            selected.append(instance)
        return {"format": "gta-sa-region-v1", "coordinate_system": "gta-z-up", "center": list(center),
                "radius": radius, "interior": interior, "instances": selected, "models": models,
                "stats": {"instances": len(selected), "models": len(models), "primitives": primitives},
                "omitted": dict(omitted), "live_dynamic_transforms_applied": False}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--game", type=Path, required=True)
    imp.add_argument("--output", type=Path, required=True)
    query = sub.add_parser("query")
    query.add_argument("--database", type=Path, required=True)
    query.add_argument("--center", type=float, nargs=3, required=True)
    query.add_argument("--radius", type=float, default=128)
    query.add_argument("--interior", type=int, default=0)
    query.add_argument("--hour", type=float, default=12)
    query.add_argument("--active-stream", action="append", default=[])
    query.add_argument("--include-dynamic", action="store_true")
    query.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "import":
        report = build_world(args.game, args.output)
        print(json.dumps({"database": str(args.output), "stats": report["stats"],
                          "interiors": report["interiors"], "conditional_streams": report["conditional_streams"],
                          "models_without_col": len(report["instances_without_col"])}))
    else:
        result = query_world(args.database, args.center, args.radius, args.interior,
                             args.active_stream, args.hour, args.include_dynamic)
        with args.output.open("xb") as stream:
            stream.write(encoded(result))
        print(json.dumps({"output": str(args.output), "stats": result["stats"], "omitted": result["omitted"]}))


if __name__ == "__main__":
    main()
