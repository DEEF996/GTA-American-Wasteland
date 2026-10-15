"""Private, disposable compiled collision/rail cache, used only by workers.

No executable serialization or archive extraction. A hit retains the original
prepared COL seal and independently revalidates the complete rail NodeArray.
The native loader still performs its original collision query controls.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import time
import uuid
import zipfile
import zlib

MAX_BYTES = 2 * 1024**3
LIMITS = {'metadata.json': 64*1024**2, 'collision.col': 16*1024**2,
          'rails.qb': 16*1024**2}
MAX_ENTRY_BYTES = sum(LIMITS.values()) + 4096
DEPENDENCIES = (
    'gta/world_tile_cache.py', 'gta/world_stream.py', 'gta/geometry.py',
    'gta/scene_export.py', 'gta/rails.py', 'gta/rail_export.py', 'gta/global_rails.py', 'gta/terrain_mapping.py', 'gta/surface_flags.py',
    'vanilla_thaw/probe_gta_scene.py', 'vanilla_thaw/probe_gta_rails.py',
    'vanilla_thaw/probe_world_rails.py', 'carentan_col.py', 'carentan_qb.py',
    'assets_export.py',
)
_NAME = re.compile(r'tile-[0-9a-f]{64}\.zip\Z')
_TEMP = re.compile(r'\.tile-[0-9a-f]{32}\.tmp\Z')
_CACHE_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError,
                 OverflowError, RuntimeError, struct.error, zipfile.BadZipFile, zlib.error)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def compiler_fingerprint():
    """Content, not mtimes: copied installations and code updates stay distinct."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name in DEPENDENCIES:
        digest.update(name.encode() + b'\0')
        digest.update(hashlib.sha256((root / name).read_bytes()).digest())
    return digest.hexdigest()


def _validate(result, identity):
    import probe_gta_scene
    inputs = result['inputs']
    payload = inputs['col']
    recipe = identity['recipe']
    selection = inputs['selection']
    if not (result['key'] == identity['key'] and
            inputs['format'] == 'gta-thaw-scene-probe-v1' and
            inputs['seal'] == probe_gta_scene._seal(inputs) and
            236 <= len(payload) <= LIMITS['collision.col'] and
            hashlib.sha256(payload).hexdigest() == inputs['col_sha256'] and
            list(inputs['coordinates']['origin_gta']) == list(recipe['origin']) and
            list(selection['center_gta']) == list(recipe['center']) and
            selection['radius_gta'] == recipe['radius'] and
            selection['interior'] == recipe['area'] and
            selection['active_streams'] == list(recipe['active_streams']) and
            result['live_collision_complete'] is False and
            result['dynamic_entities_reconciled'] is False):
        raise ValueError('Compiled tile does not match its prepared collision seal/selection')
    if recipe['rail_cache_sha256'] is not None:
        from probe_world_rails import prepare_rail_validation
        # This parses all actual QB nodes; a persisted validation seal alone
        # cannot authorize rail bytes read from a previous run.
        if inputs['rails']['validation'] != prepare_rail_validation(inputs):
            raise ValueError('Compiled tile rail validation changed')
    elif 'rails' in inputs:
        raise ValueError('Unexpected cached rail graph')


class TileCache:
    def __init__(self, request):
        self.root = Path(request['database']).resolve().parent / 'compiled-tiles'
        recipe = dict(request['recipe'])
        # The request key already contains the exact local visibility identity;
        # raw hour remains on the live Ticket, as with the resident tile cache.
        recipe.pop('hour')
        self.identity = None
        self.path = None
        try:
            self.identity = json.loads(_encoded(dict(format='gta-thaw-compiled-tile-v1',
                key=request['key'], compiler=compiler_fingerprint(), recipe=recipe)))
            self.path = self.root / ('tile-' + hashlib.sha256(_encoded(self.identity)).hexdigest() + '.zip')
        except _CACHE_ERRORS:
            pass  # Missing/unreadable cache or compiler source never blocks a tile.

    def _safe_directory(self):
        return (not self.root.is_symlink() and not self.root.is_junction() and
                self.root.resolve().parent == self.root.parent)

    def load(self):
        if self.path is None:
            return None
        try:
            if (not self._safe_directory() or self.path.is_symlink() or
                    self.path.stat().st_size > MAX_ENTRY_BYTES):
                return None
            with zipfile.ZipFile(self.path) as archive:
                members = archive.infolist()
                names = [item.filename for item in members]
                if (len(names) != len(set(names)) or
                        set(names) not in ({'metadata.json', 'collision.col'}, set(LIMITS)) or
                        any(item.file_size > LIMITS[item.filename] or item.flag_bits & 1
                            for item in members)):
                    return None
                metadata = json.loads(archive.read('metadata.json'))
                if metadata['identity'] != self.identity:
                    return None
                result = metadata['result']
                result['inputs']['col'] = archive.read('collision.col')
                if 'rails.qb' in names:
                    result['inputs']['rails']['source'] = archive.read('rails.qb')
            _validate(result, self.identity)
            try:
                os.utime(self.path, None)  # Last successful use, for eviction.
            except OSError:
                pass  # Read-only caches can still supply fully verified hits.
            return result
        except _CACHE_ERRORS:
            return None

    def _trim(self):
        """Only our direct files; never follow links or remove unrelated data."""
        entries = []
        for path in self.root.iterdir():
            if path.is_symlink() or path.is_junction() or not path.is_file():
                continue
            try:
                stat = path.stat()
                if _NAME.fullmatch(path.name):
                    entries.append((stat.st_mtime_ns, path, stat.st_size))
                elif _TEMP.fullmatch(path.name) and time.time()-stat.st_mtime > 3600:
                    path.unlink()  # An interrupted writer from an older run.
            except OSError:
                continue
        total = sum(item[2] for item in entries)
        for _, path, size in sorted(entries):
            if total <= MAX_BYTES:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                continue

    def save(self, result):
        if self.path is None:
            return False
        temporary = None
        try:
            if not self._safe_directory():
                return False
            self.root.mkdir(exist_ok=True)
            metadata = deepcopy(result)
            blobs = {'collision.col': metadata['inputs'].pop('col')}
            rails = metadata['inputs'].get('rails')
            if rails is not None and rails['source'] is not None:
                blobs['rails.qb'] = rails.pop('source')
            blobs['metadata.json'] = _encoded(dict(identity=self.identity, result=metadata))
            if any(len(value) > LIMITS[name] for name, value in blobs.items()):
                return False
            temporary = self.root / ('.tile-' + uuid.uuid4().hex + '.tmp')
            with temporary.open('xb') as stream:
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                    for name, value in blobs.items():
                        archive.writestr(name, value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._trim()
            return True
        except _CACHE_ERRORS:
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
