"""Original stock PC, text pool and authored permanent-font prerequisites.

The retained child owns the native objects. This module neither changes stock
font capacity nor supplies replacement font data, scripts, colors or callbacks.
Global.pak must be loaded by its original API between run_sprites and run.
"""
import hashlib
import json
from pathlib import Path
import struct

import probe_core
import probe_loader as host
import probe_native

SOURCE_NAME = "scripts/game/startup.qb.wpc"
SOURCE_SHA = "907aa1e79e6445a7b47cf9959d7d4a17c323d406f0229be9bb1fb94d7bcc413a"
SOURCE_BYTES, SCRIPT_KEY, BODY_BYTES = 5956, 0xae754239, 1409
INSTALL_KEY = "Software\\Aspyr\\Tony Hawk's American Wasteland\\Install"
FONT_MAP, TEXT_POOL = 0x768c10, 0x768a20

# Protected entry identities have separate original caller evidence: parser
# 413121 (whole ScriptUpdate pin), lookup 4FF639, text pool startup 4CF080.
# The font vtable is corroborated by the original spacing/color methods and
# original destructor store; TextInstance/backend constructors store theirs.
PINS = {
    "PCInitialize": ("OriginalPCInitialize", 0x628560, 112, "0a1b8f279991d07901e954b56843fd51d867056c64de6cfd163977852345db4e"),
    "PCRegistryQuery": ("OriginalPCRegistryQuery", 0x628490, 203, "7d0f2d13226a7652174d6be7242ee5dadcbe369c9273ff0e20531d2c8ce9801d"),
    "FontRegistryCRT": ("OriginalFontRegistryCRT", 0x688ee0, 26, "94249df3bc29dc4a04c7e8e9c3103cc380df8aecccf425f2b417fa1e903834bf"),
    "TextPoolInit": ("OriginalTextPoolInit", 0x4d0750, 5, "d8203b3158478f239c92098fc4fdeb9944aa6b892c43d13b0946fb8dbb27b55a"),
    "TextPoolBody": ("OriginalTextPoolBody", 0x509ba0, 131, "471b9ba2bd1fde8d5d3ebf5e83a92fb16bfcaff1307cef45322a1a86709761cb"),
    "ParseParameters": ("OriginalParseParameters", 0x40efe0, 8, "7b4b110d469e7972829ae08ea3038ebffd88c59e1f1ad5280110cc395dab1258"),
    "StructCtor": ("OriginalFontParametersCtor", 0x414230, 11, "219b92061b08d719b045423de28a365462f20df22e2ca28f240d235c37052a90"),
    "StructClear": ("OriginalFontParametersClear", 0x4152d0, 147, "f23b53dba87191e649a66817212cb7591e3991ac5388a779046eb5ebddc3f331"),
    "GetString": ("OriginalParameterGetString", 0x414dc0, 20, "82f04b47e2cbf8b895bc0bf60df49f8e10f2e0fc3024d4dfd4349c37d77523b2"),
    "LoadFont": ("OriginalLoadFont", 0x4d0d50, 344, "9e1fcb10b88303667095b2d87839411df061ed9b5b5b1f40582ddbe4ae047a71"),
    "Theme": ("OriginalThemeGetFontColors", 0x5ac2f0, 47, "a99e4b8c9fb12b5a709311efafa5dd182aed25d3235ca3363e3c74738130aee3"),
    "FontLookup": ("OriginalFontLookup", 0x4d0930, 6, "5b3e1a7c4ac7c37ea058cbcb0bffb1cca7765905aca85ee2b40cd2a6ac06e3a8"),
    "FontLookupCaller": ("OriginalFontLookupCaller", 0x4ff620, 42, "0c49bf5b018c20e5da4a25afb593c2b180adc1727028ae941a00871608acb057"),
    "FontVtable": ("OriginalFontVtable", 0x694db4, 28, "bb0c869a6f960974b7ee3c673306701ed198c50e02b566f0f552b3987dfe1f0e"),
    "TextVtable": ("OriginalTextVtable", 0x694ddc, 20, "3b69325e5294de6187d9d489c41eb3688f03fbafbeb28a6880b8cacc54c95663"),
    "TextBackendVtable": ("OriginalTextBackendVtable", 0x6955e0, 20, "2a2fca3ca976a16215642730a3c1a24c86338cec4f54d016ba602b8f6f46e5e8"),
}
# Includes the terminating original newline. No script bytes are embedded.
PARAMETERS = [
    ("Theme", 1121, 1132, "ab3eca5f9ace0e3552623fbf0bf3022a00e0dc631a8ed49ffea912897de1cf57", None, None),
    ("testtitle", 1138, 1187, "748970df570510011da984eccf74f1b3c8db7dd4b16c5719703a13fbcba83850", -2, 8),
    ("newtrickfont", 1193, 1244, "84783fd8976af923e2d7a9cf8ae1242d5883c311c2417751ae4183abb0e7ad1a", 0, 8),
    ("small", 1250, 1302, "1adbaf43e3ec9da480938077cefdc6b88f35346bfb9ab44e6f04d2296dcb005c", 2, 3),
    ("newtimerfont", 1308, 1337, "c724b652713e48143831a06ec7c51cf698b2ddff4339e1330ccdbe3cdb06f09a", -2, None),
    ("dialog", 1343, 1396, "0082b588d166be13116822929a18d79518571cb64cfebcb01ed1a96df8a38ac4", -1, 8),
    ("ButtonsPC", 1402, 1407, "470247fd093a984c63e3779141c65ce3268495b35e3e2b96165d638ac1cf57de", 0, None),
]


def validate_source(source):
    host.require(len(source) == SOURCE_BYTES and hashlib.sha256(source).hexdigest() == SOURCE_SHA,
                 "Whole stock startup source differs")
    host.require(struct.unpack_from("<I", source, 744)[0] == SCRIPT_KEY and
                 struct.unpack_from("<2I", source, 764) == (BODY_BYTES, 971),
                 "Whole original permanent-assets script header differs")
    return source


def read_inputs(qb_manifest, image_manifest):
    value = json.loads(qb_manifest.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    directory = qb_manifest.resolve().parent
    original = Path(image["source"]).resolve()
    host.require(value.get("format") == "stock-thaw-core-qb-cache-v1" and
                 value.get("sources_revalidated_before_publish") is True and
                 Path(value["source_directory"]).resolve() == original and not directory.is_relative_to(original),
                 "Font startup must belong to the selected separate stock cache")
    rows = [row for row in value["members"] if row["order"] == 23]
    host.require(len(rows) == 1 and rows[0]["canonical_name"] == SOURCE_NAME and
                 rows[0]["bytes"] == SOURCE_BYTES and rows[0]["sha256"] == SOURCE_SHA,
                 "Stock font startup archive member differs")
    source = (directory / rows[0]["cache_file"]).resolve()
    host.require(source.is_relative_to(directory), "Stock font source escapes its private cache")
    return validate_source(source.read_bytes())


def registry_snapshot():
    """Require existing keys: original RegCreateKeyA must not create either."""
    import winreg
    result = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        with winreg.OpenKey(root, INSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
            info = winreg.QueryInfoKey(key)
            host.require(info[0] <= 64 and info[1] <= 64, "Stock installation key exceeds snapshot bound")
            result.append((info, sorted(winreg.EnumValue(key, index) for index in range(info[1]))))
    return result


def _access(api, process, record, deadline, log):
    pins = {**host.QB_PINS, **host.VM_PINS, **probe_native.PINS, **PINS}

    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    return read, word, call


def _check_pins(read):
    for name, address, size, digest in PINS.values():
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest,
                     "Original font prerequisite differs: " + name)


def run_platform(api, process, record, deadline, log):
    read, word, call = _access(api, process, record, deadline, log)
    _check_pins(read)
    state = (word(0x844230), word(0x844228), read(0x6d8679, 1))
    log("font_pc_platform_before", platform=state[0], language=state[1],
        original_flag=state[2][0], installation_key_pointer=word(0x6d8688))
    if state[0] == 7 and state[1] in (2, 3, 5, 9) and state[2] == b"\1":
        log("font_pc_platform_reused", platform=7, language=state[1], original_state_reused=True)
        return
    # Platform/language are zero-initialized BSS, but the stock .data flag is
    # already 1 in the pinned PE before its original initializer executes.
    host.require(state == (0, 0, b"\1") and word(0x6d8688) == 0x6a23e4,
                 "Original PC platform state is neither cold nor initialized")
    host.require(read(0x6a23e4, len(INSTALL_KEY) + 1) == INSTALL_KEY.encode("ascii") + b"\0",
                 "Original PC registry path differs")
    before = registry_snapshot()
    call("PCInitialize")
    host.require(registry_snapshot() == before, "Original PC query changed an installation registry key")
    host.require(word(0x844230) == 7 and word(0x844228) in (2, 3, 5, 9) and read(0x6d8679, 1) == b"\1",
                 "Original PC platform/language initialization failed")
    log("font_pc_platform_pass", platform=7, language=word(0x844228), original_initializer=True,
        existing_registry_keys=2, installation_registry_unchanged=True, registry_api_substituted=False)


def text_instances(words, first, count):
    result = set()
    while first:
        host.require(len(result) < count and first not in result, "Native text pool is cyclic or oversized")
        result.add(first)
        text = words(first, 20)
        backend = words(text[19], 19)
        host.require(text[0] == 0x694ddc and backend[0] == 0x6955e0 and backend[2] & 0xff == 1,
                     "Original text or disabled backend class differs")
        first = text[18]
    host.require(len(result) == count, "Native text pool did not construct its complete authored count")
    return result


def run_sprites(api, process, record, deadline, log):
    read, word, call = _access(api, process, record, deadline, log)
    _check_pins(read)
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     "Original frontend object leaves its native arena")
        return struct.unpack(f"<{count}I", read(address, count * 4))

    def callbacks():
        begin, current = word(0x8505f0), word(0x8505ec)
        host.require(begin >= 0x10000 and begin % 4 == current % 4 == 0 and 0 <= current - begin <= 1024,
                     "Original retained CRT exit table is unavailable or oversized")
        return struct.unpack(f"<{(current - begin) // 4}I", read(begin, current - begin))

    configuration = struct.unpack("<7I", read(0x7740e0, 28))
    log("font_sprites_before", engine_configuration=list(configuration),
        text_pool=word(TEXT_POOL), font_map=list(struct.unpack("<6I", read(FONT_MAP, 24))),
        crt_exit_begin=word(0x8505f0), crt_exit_current=word(0x8505ec))
    # Stock engine_params' XBox record has 350; reTHAWed's 5000 is not stock.
    host.require(word(TEXT_POOL) == word(FONT_MAP + 4) == word(FONT_MAP + 8) == 0 and
                 configuration == (60, 350, 3, 3, 0x447a0000, 40, 100),
                 "Original font/text pools already active or whole engine configuration absent")
    before = callbacks()
    host.require(0x68cf30 not in before, "Original font CRT was already registered")
    call("FontRegistryCRT")
    host.require(callbacks() == before + (0x68cf30,) and word(FONT_MAP) == 4 and
                 word(FONT_MAP + 8) == 0 and read(FONT_MAP + 0x14, 3) == bytes(3) and
                 not any(words(word(FONT_MAP + 4), 48)), "Original 16-bucket font registry/CRT differs")
    call("TextPoolInit", (word(0x7740e4),))
    instances = text_instances(words, word(TEXT_POOL), configuration[1])
    _check_pins(read)
    log("font_sprites_pass", native_text_instances=len(instances), inactive_backends=len(instances),
        font_registry_buckets=16, stock_font_capacity=16, capacity_patches=False,
        original_font_crt_registered=True, lifetime="retained child process")
    return {"text_instances": len(instances), "font_map": FONT_MAP}


def validate_parameters(body):
    host.require(len(body) == BODY_BYTES, "Whole cached stock startup body differs")
    for name, begin, end, digest, _, _ in PARAMETERS:
        function = PINS["Theme" if name == "Theme" else "LoadFont"][1]
        host.require(body[begin - 5] == 0x43 and struct.unpack_from("<I", body, begin - 4)[0] == function and
                     hashlib.sha256(body[begin:end + 1]).hexdigest() == digest,
                     "Original cached font call or whole authored parameters differ: " + name)


def run(api, process, record, deadline, log, source, preloaded):
    validate_source(source)
    read, word, call = _access(api, process, record, deadline, log)
    _check_pins(read)
    source_pointer = probe_core.reuse(api, process, preloaded, SOURCE_NAME, source)
    symbol = call("lookup", (SCRIPT_KEY,))
    host.require(symbol == source_pointer + 740 and struct.unpack("<4I", read(symbol, 16)) ==
                 (0x70400, SCRIPT_KEY, probe_native.qbkey(SOURCE_NAME), source_pointer + 760),
                 "Original permanent-assets source provenance differs")
    host.require(word(FONT_MAP) == 4 and word(FONT_MAP + 4) != 0 and word(TEXT_POOL) != 0 and
                 word(0x844230) == 7 and word(0x6dcf84) == 0,
                 "Fonts require genuine PC, font/text pools and idle current-script context")
    arena_start, arena_end = word(0x84376c), word(0x843770)

    def bounded(address, size):
        host.require(address % 4 == 0 and arena_start <= address <= arena_end - size,
                     "Original font or script object leaves its native arena")
        return read(address, size)

    cache = word(0x6dcfac)
    cached = call("ScriptCacheLoad", (SCRIPT_KEY,), cache)
    body = bounded(cached, BODY_BYTES)
    validate_parameters(body)
    baseline = (word(0x6dcf8c), word(0x6dcf90))
    host.require(baseline[1] <= 4096 and word(0x6dcf84) == 0, "Original script registry is busy or oversized")
    call("HeapContextPush", (word(0x842b18 + 0x128),), 0x842b18)
    script = call("ScriptAllocate", (0xfc, 1))
    bounded(script, 0xfc)
    host.require(call("ScriptCtor", this=script) == script, "Original font context Script constructor failed")
    call("ScriptSetSource", (259952684, 0, 0, 0), script)
    call("HeapContextPop", this=0x842b18)
    locals_ = word(script + 0x1c)
    bounded(locals_, 8)
    host.require(word(script + 0xd0) == 259952684 and word(0x6dcf90) == baseline[1] + 1,
                 "Original NullScript font context was not bound")
    owned = host.success(api.VirtualAllocEx(process, None, 4096, 0x3000, 4), "Allocate owned font parameter headers")
    params, scratch = owned, owned + 16
    host.require(call("StructCtor", this=params) == params and read(params, 8) == bytes(8),
                 "Original font parameter constructor failed")
    results = []
    for name, begin, end, _, char_spacing, space_spacing in PARAMETERS:
        host.remote_write(api, process, scratch, bytes(16))
        next_ = call("ParseParameters", (params, cached + begin, locals_, scratch))
        host.require(next_ == cached + end and word(scratch) == 0 and word(params + 4) != 0,
                     "Original parser did not consume all authored parameters: " + name)
        if name == "Theme":
            host.require(call("Theme", (params, script)) & 0xff != 0 and
                         call("StructGetChecksum", (0x16af73ab, scratch, 1), locals_) & 0xff != 0 and
                         word(scratch) == 0x64d11782, "Original Theme did not populate genuine script locals")
        else:
            if name != "ButtonsPC":
                host.require(call("GetString", (0, scratch, 0), params) & 0xff != 0 and
                             read(word(scratch), len(name) + 1) == name.encode("ascii") + b"\0",
                             "Original parsed font filename differs")
            host.require(call("LoadFont", (params, script)) & 0xff != 0, "Original LoadFont callback failed")
            font = call("FontLookup", (probe_native.qbkey(name),))
            wrapper = bounded(font, 12)
            host.require(struct.unpack_from("<I", wrapper)[0] == 0x694db4, "Original font lookup class differs")
            data = struct.unpack_from("<I", wrapper, 8)[0]
            header = bounded(data, 0x17c)
            char, space = struct.unpack_from("<hh", header, 0x134)
            host.require(char == char_spacing and (space_spacing is None or space == space_spacing),
                         "Native font spacing differs from the original authored parameters")
            texture = struct.unpack_from("<I", header, 0x178)[0]
            gpu = struct.unpack_from("<I", bounded(texture, 0x2c), 0x14)[0]
            host.require(gpu >= 0x10000, "Original font did not create its native GPU texture")
            results.append({"name": name, "font": font, "data": data, "gpu_texture": gpu,
                            "character_spacing": char, "space_spacing": space})
        call("StructClear", this=params)
        host.require(read(params, 8) == bytes(8), "Original parameter cleanup left fields")
    host.require(len({row["font"] for row in results}) == 6, "Stock font identities are not distinct")
    call("ScriptDtor", this=script)
    call("ScriptFree", (script,))
    call("ScriptCacheReleaseReference", (SCRIPT_KEY,), cache)
    host.require((word(0x6dcf8c), word(0x6dcf90)) == baseline and word(0x6dcf84) == 0,
                 "Original font context cleanup did not restore script registry")
    _check_pins(read)
    log("font_startup_pass", source=SOURCE_NAME, source_sha256=SOURCE_SHA, whole_body_bytes=BODY_BYTES,
        fonts=results, original_parameter_parser=True, original_theme_callback=True,
        source_script_updates=0, script_registry_restored=True, stock_font_capacity=16,
        replacement_font_data=False, capacity_patches=False, backend_ready=False)
    return results
