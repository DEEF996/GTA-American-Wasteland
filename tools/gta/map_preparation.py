"""Prepare the supported on-disk GTA map once, before a backend is started.

Immutable world/rail generations are published by one atomic pointer update.
Original game inputs are only read. Mod Loader's virtual map substitutions are
not interpreted by the on-disk IPL/COL importer.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

import geometry
import global_rails
from file_hash_cache import FileHashCache, file_stamp

FORMAT = 'gta-thaw-world-generation-v1'
_GENERATION = re.compile(r'map-[0-9a-f]{64}\Z')
_SCRATCH = re.compile(r'(?:build|staging)-[0-9a-f]{32}\Z')


class PreparationCancelled(RuntimeError):
    pass


def _check(stopped):
    if stopped is not None and stopped():
        raise PreparationCancelled('Map preparation cancelled before publication')


def _direct(path, parent):
    if (path.is_symlink() or path.is_junction() or path.resolve().parent != parent.resolve()):
        raise ValueError('Redirected private map-cache path: ' + str(path))
    return path


def _cache(game):
    game = Path(game).resolve(strict=True)
    cache = _direct(game / 'thaw_gta', game)
    manifest = json.loads((cache / 'base-copy.json').read_text(encoding='utf-8-sig'))
    if not (manifest.get('format') == 'gta-thaw-isolated-copy-v1' and
            Path(manifest['game']).resolve() == game and
            Path(manifest['source']).resolve() != game):
        raise ValueError('Map preparation requires the verified isolated GTA copy')
    return game, cache


def _pointer(cache):
    path = _direct(cache / 'world-current.json', cache)
    if not path.exists():
        return None
    if path.stat().st_size > 4096:
        raise ValueError('World-generation pointer is oversized')
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get('format') != FORMAT or 'generation' not in value or
            any(not isinstance(value.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', value[key])
                for key in ('world_sha256', 'rail_sha256')) or
            (value.get('generation') is not None and
             (not isinstance(value['generation'], str) or not _GENERATION.fullmatch(value['generation'])))):
        raise ValueError('Invalid world-generation pointer')
    return value


def _database(cache, pointer):
    parent = cache
    if pointer is not None and pointer['generation'] is not None:
        generations = _direct(cache / 'world-generations', cache)
        parent = _direct(generations / pointer['generation'], generations)
    return _direct(parent / 'world.db', parent)


def database_path(game):
    """Resolve a published pair; WorldStream retains its own full identity checks."""
    _, cache = _cache(game)
    return _database(cache, _pointer(cache))


def _reject_virtual_maps(game):
    if not any((game / folder / 'modloader.asi').is_file() for folder in ('', 'scripts', 'plugins')):
        return
    root = game / 'modloader'
    if not root.exists():
        return
    if root.is_symlink() or root.is_junction():
        raise ValueError('Unsupported redirected Mod Loader source; only on-disk GTA map inputs are supported')
    count = 0
    def read_error(error):
        raise error
    for directory, folders, files in os.walk(root, followlinks=False, onerror=read_error):
        for name in folders:
            child = Path(directory) / name
            if child.is_symlink() or child.is_junction():
                raise ValueError('Unsupported redirected Mod Loader source: ' + str(child))
        count += len(folders) + len(files)
        if count > 100000:
            raise ValueError('Mod Loader source inventory exceeds the preparation bound')
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() in ('.ipl', '.ide', '.col', '.img') or name.lower() in (
                    'gta.dat', 'default.dat', 'object.dat'):
                raise ValueError('Unsupported possible Mod Loader map override: ' +
                    path.relative_to(game).as_posix() +
                    '. Preparation supports the actual on-disk DAT/IDE/IPL/COL/IMG files only.')


def source_manifest(game, *, stopped=None, digests=None):
    """Rediscover references, including newly added DAT entries, before hashing."""
    _reject_virtual_maps(game)
    references = geometry.data_references(game)
    archives = list(dict.fromkeys([game / 'models/gta3.img', game / 'models/gta_int.img', *references['IMG']]))
    paths = list(dict.fromkeys([game / 'data/default.dat', game / 'data/gta.dat', game / 'data/object.dat',
        *references['IDE'], *references['IPL'], *references['COLFILE'], *archives]))
    result, stamps = [], []
    for path in paths:
        _check(stopped)
        if not path.is_file() or not path.resolve().is_relative_to(game):
            raise ValueError('Missing or redirected GTA map input: ' + str(path))
        before = _source_stamp(path)
        digest = geometry.file_digest(path) if digests is None else digests.digest(path)
        after = _source_stamp(path)
        if before != after:
            raise ValueError('GTA map input changed during preparation: ' + str(path))
        result.append(dict(path=path.relative_to(game).as_posix(), size=after[0], sha256=digest))
        stamps.append((path, after))
    for path, previous in stamps:
        if _source_stamp(path) != previous:
            raise ValueError('GTA map input changed while other sources were being checked: ' + str(path))
    return sorted(result, key=lambda row: row['path'])


def _source_stamp(path):
    status = path.stat()
    # Windows st_ctime is creation time; ChangeTime also catches edits that
    # restore the original size and mtime after modifying a map archive.
    return status.st_size, status.st_mtime_ns, status.st_ctime_ns, file_stamp(path)


def _pair(database):
    database = _direct(database, database.parent)
    rail_path = _direct(database.with_name('global_rails.db'), database.parent)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        metadata = {key: json.loads(value) for key, value in db.execute('SELECT key,value FROM metadata')}
        if metadata.get('schema') != geometry.SCHEMA or metadata.get('complete') is not True:
            raise ValueError('Incomplete world database')
        sources = sorted(metadata['report']['source_files'], key=lambda row: row['path'])
    digests = FileHashCache(database.parent / '.validated-map-hashes.json')
    world_sha = digests.digest(database)
    rail_sha, _ = global_rails.cache_identity(rail_path, world_sha256=world_sha, digest=digests.digest)
    digests.save()
    return dict(world_sha256=world_sha, rail_sha256=rail_sha), sources


def _publish(cache, pointer):
    temporary = cache / ('.world-current-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(geometry.encoded(dict(format=FORMAT, **pointer)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _direct(cache / 'world-current.json', cache))
    finally:
        temporary.unlink(missing_ok=True)


def _cleanup(path, parent):
    # Recursive cleanup is restricted to this invocation's checked staging dir.
    if path is not None and path.exists():
        _direct(path, parent)
        if not _SCRATCH.fullmatch(path.name):
            raise ValueError('Refusing cleanup outside the owned preparation directory')
        shutil.rmtree(path)


def prepare(game, scratch, *, progress=None, stopped=None):
    """Call once per supervisor startup, while no original backend is running."""
    game, cache = _cache(game)
    scratch = Path(scratch).resolve()
    if scratch.is_relative_to(game):
        raise ValueError('Map build scratch directory must be outside the game installation')

    def report(stage, **details):
        _check(stopped)
        if progress is not None:
            progress(stage, **details)

    report('checking_sources')
    digests = FileHashCache(cache / 'map-source-hashes.json')
    sources = source_manifest(game, stopped=stopped, digests=digests)
    try:
        pointer = _pointer(cache)
        current = _database(cache, pointer)
        identity, old_sources = _pair(current)
        if pointer is not None and any(pointer[key] != value for key, value in identity.items()):
            raise ValueError('Published world-generation hashes changed')
        if old_sources == sources:
            digests.save()
            report('map_prepared', changed=False, source_count=len(sources))
            return dict(changed=False, database=str(current), source_count=len(sources),
                        source_validation=dict(digests.stats), **identity)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        pass  # Rebuild an incomplete/corrupt private pair, never use it for skating.

    build = staging = None
    generations = _direct(cache / 'world-generations', cache)
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        build = scratch / ('build-' + uuid.uuid4().hex)
        build.mkdir()
        world = build / 'world.db'
        report('building_world')
        result = geometry.build_world(game, world)
        if sorted(result['source_files'], key=lambda row: row['path']) != sources:
            raise ValueError('GTA map sources changed while the world was being imported')
        report('building_rails')
        global_rails.build_world_rails(world, world.with_name('global_rails.db'),
            progress=lambda counts: report('building_rails', counts=counts))
        identity, imported_sources = _pair(world)
        if imported_sources != sources or source_manifest(game, stopped=stopped, digests=digests) != sources:
            raise ValueError('GTA map sources changed before publication')
        report('publishing')
        generations.mkdir(exist_ok=True)
        staging = generations / ('staging-' + uuid.uuid4().hex)
        staging.mkdir()
        for name in ('world.db', 'global_rails.db'):
            shutil.copyfile(build / name, staging / name)
        if _pair(staging / 'world.db') != (identity, sources):
            raise ValueError('Copied world/rail pair failed identity validation')
        generation = 'map-' + hashlib.sha256(geometry.encoded(identity)).hexdigest()
        destination = _direct(generations / generation, generations)
        if destination.exists():
            try:
                reusable = _pair(destination / 'world.db') == (identity, sources)
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                reusable = False
            if not reusable:
                # Repair a corrupt generation by publishing a new immutable
                # directory; never overwrite a currently published pair.
                generation = 'map-' + hashlib.sha256(geometry.encoded(identity)+uuid.uuid4().bytes).hexdigest()
                destination = _direct(generations / generation, generations)
        if not destination.exists():
            os.rename(staging, destination)
            staging = None
        _check(stopped)
        _publish(cache, dict(generation=generation, **identity))
        digests.save()
        report('map_prepared', changed=True, source_count=len(sources))
        return dict(changed=True, database=str(destination / 'world.db'), source_count=len(sources),
                    source_validation=dict(digests.stats), **identity)
    finally:
        _cleanup(staging, generations)
        _cleanup(build, scratch)
