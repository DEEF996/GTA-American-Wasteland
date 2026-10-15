"""Build a private CPython runtime; never install Python or change PATH/registry.

The output has the fixed package location runtime/python. Its isolated search
path also covers the package's Python backend and spawned compiler workers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from install import plain_path

VERSION = '3.13.16'
ARCHIVE = 'python-3.13.16-amd64.zip'
URL = 'https://www.python.org/ftp/python/3.13.16/' + ARCHIVE
SHA256 = 'bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159'
UPSTREAM_MANIFEST = 'https://www.python.org/ftp/python/3.13.16/windows-3.13.16.json'
UPSTREAM_MANIFEST_SHA256 = 'e95d3e68177b2c459f3b6415245dae02357018d9b0c8de85fd0c877996688c09'
MAX_DOWNLOAD, MAX_EXPANDED, MAX_MEMBER, MAX_MEMBERS = 128*1024**2, 512*1024**2, 64*1024**2, 20000
SEARCH_PATH = '.\nLib\nDLLs\n../../tools/gta\n../../tools/vanilla_thaw\n../../tools\n'
ROOT_FILES = {'python.exe', 'pythonw.exe', 'python3.dll', 'python313.dll',
              'vcruntime140.dll', 'vcruntime140_1.dll', 'license.txt'}
LIB_EXCLUDED = {'__pycache__', 'site-packages', 'ensurepip', 'idlelib', 'test', 'tests'}
REQUIRED = {'python.exe', 'python313.dll', 'license.txt', 'lib/encodings/__init__.py',
            'lib/tkinter/__init__.py', 'dlls/_tkinter.pyd', 'dlls/tcl86t.dll',
            'dlls/tk86t.dll', 'dlls/_ssl.pyd', 'dlls/_sqlite3.pyd',
            'tcl/tcl8.6/init.tcl', 'tcl/tk8.6/tk.tcl'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def official_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'www.python.org' or
            not parsed.path.startswith('/ftp/python/') or parsed.query or parsed.fragment):
        raise ValueError('Runtime download must stay on official HTTPS Python hosting')


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(cache, *, offline=False):
    cache = plain_path(cache, missing=True)
    cache.mkdir(parents=True, exist_ok=True)
    target = plain_path(cache/ARCHIVE, missing=True)
    if target.exists():
        if target.stat().st_size > MAX_DOWNLOAD or digest(target) != SHA256:
            raise ValueError('Cached Python archive checksum or size differs')
        return target
    if offline:
        raise ValueError('Offline Python archive is missing: '+str(target))
    official_url(URL)
    temporary = None
    try:
        request = Request(URL, headers={'User-Agent': 'GTA-THAW-runtime-packager/1'})
        with build_opener(OfficialRedirect()).open(request, timeout=60) as incoming:
            official_url(incoming.geturl())
            with tempfile.NamedTemporaryFile(dir=cache, prefix='.python-', delete=False) as output:
                temporary = Path(output.name)
                size, checksum = 0, hashlib.sha256()
                while block := incoming.read(1024*1024):
                    size += len(block)
                    if size > MAX_DOWNLOAD:
                        raise ValueError('Python download exceeds its size limit')
                    output.write(block); checksum.update(block)
        if checksum.hexdigest() != SHA256:
            raise ValueError('Official Python archive checksum differs')
        if target.exists():
            raise FileExistsError('Python cache destination appeared during download')
        os.rename(temporary, target); temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def selected(name):
    parts = PurePosixPath(name).parts
    folded = tuple(part.casefold() for part in parts)
    if name.casefold() in ('doc/html/license.html', 'doc/html/copyright.html'):
        return True
    if len(parts) == 1:
        return folded[0] in ROOT_FILES
    if folded[0] == 'lib':
        return not set(folded[1:]) & LIB_EXCLUDED and not name.endswith(('.pyc', '.pyo'))
    if folded[0] == 'dlls':
        return (len(parts) == 2 and PurePosixPath(name).suffix.casefold() in ('.dll', '.pyd')
                and not folded[-1].startswith(('_test', '_ctypes_test')))
    return folded[0] == 'tcl'


def inventory(bundle):
    """Validate all archive paths before extracting even an allowlisted member."""
    entries = bundle.infolist()
    if len(entries) > MAX_MEMBERS or sum(item.file_size for item in entries) > MAX_EXPANDED:
        raise ValueError('Python archive exceeds its inventory limit')
    seen, files = {}, []
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'{kind}{n}' for kind in ('com', 'lpt') for n in range(1,10)}
    for item in entries:
        raw = item.orig_filename
        name = raw[:-1] if item.is_dir() else raw
        parts = name.split('/')
        mode = stat.S_IFMT(item.external_attr >> 16)
        if (not name or '\\' in raw or any(ord(c) < 32 for c in raw) or
                any(not part or part in ('.','..') or any(c in part for c in '<>:"|?*') or part != part.rstrip(' .') or
                    part.split('.')[0].casefold() in reserved for part in parts) or
                mode not in (0, stat.S_IFREG, stat.S_IFDIR) or
                item.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT or
                item.flag_bits & 1 or item.file_size > MAX_MEMBER):
            raise ValueError('Unsafe or oversized Python archive member: '+raw)
        key = name.casefold()
        if key in seen:
            raise ValueError('Duplicate Python archive path: '+raw)
        seen[key] = item.is_dir()
        if not item.is_dir() and selected(name):
            files.append(item)
    for name in seen:
        for parent in PurePosixPath(name).parents:
            if str(parent) in seen and seen[str(parent)] is False:
                raise ValueError('Python archive file also used as directory: '+str(parent))
    if not REQUIRED <= {item.filename.casefold() for item in files}:
        raise ValueError('Python archive lacks required full runtime, Tk or licenses')
    return sorted(files, key=lambda item: item.filename.casefold())


def stage(output, cache, *, offline=False):
    """Create NEW <package>/runtime/python and return per-file manifest rows."""
    output = plain_path(output, missing=True).resolve()
    cache = plain_path(cache, missing=True).resolve()
    if output.exists():
        raise ValueError('Python runtime output must be a new directory')
    if output.name != 'python' or output.parent.name != 'runtime':
        raise ValueError('Python runtime requires the package layout runtime/python')
    if cache.is_relative_to(output):
        raise ValueError('Python archive cache must be outside the output')
    archive = download(cache, offline=offline)
    # Recheck the exact opened stream, avoiding a cache replacement between
    # download verification and ZIP parsing.
    with plain_path(archive).open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != SHA256:
            raise ValueError('Python archive changed before extraction')
        source.seek(0)
        with zipfile.ZipFile(source) as bundle:
            files = inventory(bundle)
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix='.python-', dir=output.parent)).resolve()
            expected = temporary
            rows = []
            def write(name, data):
                target = plain_path(temporary/name, missing=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(data)
                rows.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
            try:
                for item in files:
                    write(item.filename, bundle.read(item))
                write('python313._pth', SEARCH_PATH.encode('ascii'))
                metadata = dict(format='gta-thaw-portable-python-v1', version=VERSION,
                    architecture='amd64', source_url=URL, source_sha256=SHA256,
                    upstream_manifest=UPSTREAM_MANIFEST,
                    upstream_manifest_sha256=UPSTREAM_MANIFEST_SHA256,
                    package_location='runtime/python', system_installation_required=False,
                    site_packages_included=False, files=rows.copy())
                write('PYTHON_RUNTIME.json', (json.dumps(metadata, indent=2)+'\n').encode('utf-8'))
                if output.exists():
                    raise FileExistsError('Python runtime output appeared during extraction')
                os.rename(temporary, output)
            finally:
                if temporary.exists():
                    # Delete only the private staging directory verified inside
                    # this exact output parent, never the destination itself.
                    plain_path(temporary)
                    if temporary.resolve() != expected or temporary.parent != output.parent:
                        raise ValueError('Python staging directory moved; refusing cleanup')
                    shutil.rmtree(temporary)
    return dict(version=VERSION, architecture='amd64', source_url=URL, source_sha256=SHA256,
                output=str(output), files=rows, bytes=sum(row['bytes'] for row in rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    result = stage(args.output, args.cache, offline=args.offline)
    print(json.dumps({**result, 'files': len(result['files'])}, indent=2))
