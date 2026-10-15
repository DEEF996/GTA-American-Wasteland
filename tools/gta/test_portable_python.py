import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import portable_python as runtime


def archive(extra=()):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as bundle:
        for name in sorted(runtime.REQUIRED):
            bundle.writestr(name, 'original '+name)
        for name, data in extra:
            info = zipfile.ZipInfo(name)
            info.filename = name  # Preserve malformed Windows separators too.
            bundle.writestr(info, data)
    return result.getvalue()


class PortablePythonTests(unittest.TestCase):
    def test_allowlist_preserves_runtime_and_licenses_without_development_packages(self):
        extras = [(name,b'contents') for name in (
            'Lib/site-packages/untrusted.pth', 'Lib/ensurepip/pip.whl', 'Lib/test/test_os.py',
            'Lib/idlelib/__init__.py', 'Lib/__pycache__/os.pyc', 'Scripts/pip.exe',
            'DLLs/_testcapi.pyd', 'DLLs/_ctypes_test.pyd', 'include/Python.h',
            'Lib/multiprocessing/spawn.py', 'DLLs/_ctypes.pyd', 'tcl/tcl8.6/license.terms',
            'Doc/html/license.html', 'Doc/html/copyright.html', 'Doc/html/tutorial.html')]
        with zipfile.ZipFile(io.BytesIO(archive(extras))) as bundle:
            names = {item.filename for item in runtime.inventory(bundle)}
        self.assertEqual(names, runtime.REQUIRED | {
            'Lib/multiprocessing/spawn.py', 'DLLs/_ctypes.pyd', 'tcl/tcl8.6/license.terms',
            'Doc/html/license.html', 'Doc/html/copyright.html'})

    def test_archive_rejects_unsafe_paths_even_when_not_selected(self):
        for name in ('../outside', '/outside', 'Lib\\escape.py', 'Lib//bad.py',
                     'Lib/./bad.py', 'Lib/name:stream', 'Lib/bad. ', 'Lib/con.txt',
                     'Lib/file\x01.py', 'Lib/file?.py', 'PYTHON.EXE', 'lib/encodings'):
            with self.subTest(name=name), zipfile.ZipFile(io.BytesIO(archive([(name,b'bad')]))) as bundle:
                with self.assertRaises(ValueError):
                    runtime.inventory(bundle)

    def test_symlink_and_reparse_member_rejected(self):
        for attributes in ((stat.S_IFLNK | 0o777)<<16, stat.FILE_ATTRIBUTE_REPARSE_POINT):
            output = io.BytesIO(archive())
            with zipfile.ZipFile(output,'a') as bundle:
                info = zipfile.ZipInfo('Lib/link.py'); info.external_attr = attributes
                bundle.writestr(info, 'outside')
            with zipfile.ZipFile(output) as bundle, self.assertRaises(ValueError):
                runtime.inventory(bundle)

    def test_archive_bounds_and_required_tk(self):
        for setting, value in (('MAX_MEMBERS',1),('MAX_EXPANDED',1),('MAX_MEMBER',1)):
            with self.subTest(setting=setting), patch.object(runtime,setting,value), \
                    zipfile.ZipFile(io.BytesIO(archive())) as bundle, self.assertRaises(ValueError):
                runtime.inventory(bundle)
        with zipfile.ZipFile(io.BytesIO(archive())) as bundle:
            bundle.filelist = [item for item in bundle.filelist if item.filename != 'dlls/_tkinter.pyd']
            with self.assertRaisesRegex(ValueError,'required'):
                runtime.inventory(bundle)

    def test_stage_isolated_paths_hashes_metadata_and_no_overwrite(self):
        payload = archive()
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime,'SHA256',hashlib.sha256(payload).hexdigest()):
            root = Path(folder); cache=root/'cache'; cache.mkdir()
            (cache/runtime.ARCHIVE).write_bytes(payload)
            output=root/'package/runtime/python'
            result=runtime.stage(output,cache,offline=True)
            self.assertEqual((output/'python313._pth').read_text(),runtime.SEARCH_PATH)
            self.assertNotIn('import site',runtime.SEARCH_PATH)
            self.assertIn('../../tools/gta\n',runtime.SEARCH_PATH)
            self.assertIn('../../tools/vanilla_thaw\n',runtime.SEARCH_PATH)
            self.assertIn('../../tools\n',runtime.SEARCH_PATH)
            metadata=json.loads((output/'PYTHON_RUNTIME.json').read_text())
            self.assertFalse(metadata['system_installation_required'])
            self.assertFalse(metadata['site_packages_included'])
            for row in result['files']:
                data=(output/row['path']).read_bytes()
                self.assertEqual(row['bytes'],len(data))
                self.assertEqual(row['sha256'],hashlib.sha256(data).hexdigest())
            self.assertIn('python313._pth',{row['path'] for row in metadata['files']})
            with self.assertRaisesRegex(ValueError,'new directory'):
                runtime.stage(output,cache,offline=True)
            self.assertEqual((cache/runtime.ARCHIVE).read_bytes(),payload)

    def test_wrong_cached_hash_or_missing_archive_never_creates_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); cache=root/'cache'; cache.mkdir(); output=root/'runtime/python'
            with self.assertRaisesRegex(ValueError,'missing'):
                runtime.stage(output,cache,offline=True)
            (cache/runtime.ARCHIVE).write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'checksum'):
                runtime.stage(output,cache,offline=True)
            self.assertFalse(output.exists())

    def test_layout_and_output_cache_separation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaisesRegex(ValueError,'layout'):
                runtime.stage(root/'wrong',root/'cache',offline=True)
            with self.assertRaisesRegex(ValueError,'outside'):
                runtime.stage(root/'runtime/python',root/'runtime/python/cache',offline=True)

    def test_download_rejects_nonofficial_redirects(self):
        for url in ('http://www.python.org/ftp/python/a.zip', 'https://evil.invalid/a.zip',
                    'https://www.python.org@evil.invalid/ftp/python/a.zip',
                    'https://www.python.org/ftp/python/a.zip?x=1'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError,'official HTTPS'):
                runtime.official_url(url)
        runtime.official_url(runtime.URL)

    def test_download_hash_and_size_failures_clean_temporary_file(self):
        payload=b'official fixture'
        for expected, limit, success in (
                (hashlib.sha256(payload).hexdigest(),100,True),
                ('0'*64,100,False),
                (hashlib.sha256(payload).hexdigest(),1,False)):
            with self.subTest(limit=limit,success=success), tempfile.TemporaryDirectory() as folder, \
                    patch.object(runtime,'SHA256',expected), patch.object(runtime,'MAX_DOWNLOAD',limit), \
                    patch.object(runtime,'build_opener') as opener:
                response=io.BytesIO(payload); response.geturl=lambda:runtime.URL
                opener.return_value.open.return_value=response
                cache=Path(folder)/'cache'
                if success:
                    self.assertEqual(runtime.download(cache).read_bytes(),payload)
                    self.assertEqual([p.name for p in cache.iterdir()],[runtime.ARCHIVE])
                else:
                    with self.assertRaises(ValueError):
                        runtime.download(cache)
                    self.assertEqual(list(cache.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
