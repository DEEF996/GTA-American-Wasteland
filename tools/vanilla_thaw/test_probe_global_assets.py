"""Synthetic original-parameter and private-input boundaries; no child/game calls."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import probe_global_assets as g


class GlobalAssetsTests(unittest.TestCase):
    def test_crt_initializer_requires_cold_map_and_real_bounded_empty_sentinel(self):
        for bad in (None, "cold", "duplicate", "table", "allocation", "links", "nil", "callback", "vm"):
            with self.subTest(bad=bad):
                memory, calls = {}, []
                def write(address, data):
                    memory.update((address + i, value) for i, value in enumerate(data))
                def put(address, *values): write(address, struct.pack(f"<{len(values)}I", *values))
                def read(address, size): return bytes(memory[address + i] for i in range(size))
                def word(address): return struct.unpack("<I", read(address, 4))[0]
                pins = {}
                for key, (name, address, size, _) in g.MAP_PINS.items():
                    data = bytes(size)  # Synthetic pins, never executable or used with a child.
                    write(address, data)
                    pins[key] = (name, address, size, hashlib.sha256(data).hexdigest())
                table = bytearray(552)
                struct.pack_into("<I", table, g.MAP_CRT_INDEX * 4, pins["ResourceMapCRT"][1])
                write(0x10000, table)
                metadata = {"crt_table": dict(va=0x10000, bytes=len(table), sha256=hashlib.sha256(table).hexdigest())}
                if bad == "table": put(0x10000 + g.MAP_CRT_INDEX * 4, 0)
                put(g.RESOURCE_MAP, 0, 1 if bad == "cold" else 0, 0)
                put(0x8505f0, 0x20000); put(0x8505ec, 0x20004); put(0x84f5c4, 0x30000)
                put(0x20000, g.MAP_DESTRUCTOR if bad == "duplicate" else 0xdead)
                put(0x6dcf84, 0, 0, 0x40000, 3)
                def native(name):
                    self.assertEqual(name, "ResourceMapCRT"); calls.append(name)
                    put(g.RESOURCE_MAP + 4, 0x50000)
                    put(0x50000, 0x50000, 0 if bad == "links" else 0x50000, 0x50000,
                        0x12345678, 0 if bad == "nil" else 0xabcd0101)
                    put(0x20004, 0 if bad == "callback" else g.MAP_DESTRUCTOR); put(0x8505ec, 0x20008)
                    if bad == "vm": put(0x6dcf90, 4)
                def platform(dll, name, args):
                    self.assertEqual((dll, name, args), ("kernel32.dll", "HeapSize", (0x30000, 0, 0x50000)))
                    return 0xffffffff if bad == "allocation" else 24
                system = SimpleNamespace(read=read, word=word, call=platform)
                with patch.object(g, "MAP_PINS", pins):
                    if bad:
                        with self.assertRaises(RuntimeError):
                            g.initialize_resource_map(system, native, lambda *a, **k: None, metadata)
                    else:
                        g.initialize_resource_map(system, native, lambda *a, **k: None, metadata)
                self.assertEqual(len(calls), 0 if bad in ("cold", "duplicate", "table") else 1)

    def body(self):
        params = b"\x1b" + struct.pack("<I", len(g.FILENAME)) + g.FILENAME
        params += b"".join(b"\x16" + struct.pack("<I", key) for key in g.FLAGS) + b"\x01"
        self.assertEqual(len(params), g.PARAM_END - g.PARAM_BEGIN + 1)
        self.assertEqual(hashlib.sha256(params).hexdigest(), g.PARAM_SHA)
        body = bytearray(g.fonts.BODY_BYTES)
        body[502:507] = b"\x43" + struct.pack("<I", g.LOAD_PAK[1])
        body[g.PARAM_BEGIN:g.PARAM_END + 1] = params
        return body

    def test_only_exact_original_callback_and_complete_parameter_span(self):
        original = self.body()
        g.validate_parameters(original)
        for offset in (502, 503, 507, 513, 537, 542, 547, 552):
            changed = bytearray(original)
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(RuntimeError):
                g.validate_parameters(changed)
        with self.assertRaises(RuntimeError): g.validate_parameters(original[:-1])

    def fields(self):
        nodes = {0x2000: [3 << 9, 0, 0x3000, 0x2010]}
        for index, key in enumerate(g.FLAGS):
            nodes[0x2010 + index * 16] = [13 << 9, 0, key, 0x2020 + index * 16 if index < 2 else 0]
        def read(address, size):
            values = {0x1000: bytes(4), 0x1004: struct.pack("<I", 0x2000), 0x3000: g.FILENAME}
            return values[address][:size]
        def words(address, count):
            self.assertEqual(count, 4)
            return nodes[address]
        return nodes, read, words

    def test_native_struct_must_keep_unnamed_filename_and_all_flags(self):
        nodes, read, words = self.fields()
        g.validate_fields(read, words, 0x1000)
        nodes[0x2010][:3], nodes[0x2030][:3] = nodes[0x2030][:3], nodes[0x2010][:3]
        g.validate_fields(read, words, 0x1000)  # CStruct field order is not part of the ABI.
        for address, field, bad in ((0x2000, 0, 4 << 9), (0x2000, 1, 7),
                                    (0x2010, 2, 0), (0x2030, 3, 0x2000), (0x2020, 3, 0)):
            previous = nodes[address][field]
            nodes[address][field] = bad
            with self.subTest(address=address, field=field), self.assertRaises(RuntimeError):
                g.validate_fields(read, words, 0x1000)
            nodes[address][field] = previous

    def test_private_archive_copy_must_match_original_and_manifest(self):
        base = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(dir=base, prefix=".global-assets-test-") as temporary:
            root = Path(temporary).resolve()
            self.assertEqual(root.parent, base)
            original, private = root / "original", root / "cache"
            pins, rows = {}, []
            for index, name in enumerate(g.PAIRS):
                data = b"synthetic bounded archive " + bytes([index])
                pins[name] = (len(data), hashlib.sha256(data).hexdigest())
                for directory in (original, private):
                    target = directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                rows.append(dict(path=name, bytes=len(data), sha256=pins[name][1]))
            manifest = dict(format="gta-thaw-private-stock-resources-v1", source=str(original),
                            directory=str(private), sources_revalidated_before_publish=True,
                            original_files_modified=False, files=rows)
            resource, image, qb = private / "manifest.json", root / "image.json", root / "qb.json"
            resource.write_text(json.dumps(manifest)); image.write_text(json.dumps(dict(source=str(original))))
            with patch.object(g.fonts, "read_inputs", return_value=b"whole source"), patch.object(g, "INPUTS", pins):
                result = g.read_inputs(qb, resource, image)
                self.assertEqual(result["files"], rows)
                path = private / g.PAIRS[0]
                path.write_bytes(b"changed cache")
                with self.assertRaises(RuntimeError): g.read_inputs(qb, resource, image)


if __name__ == "__main__": unittest.main()
