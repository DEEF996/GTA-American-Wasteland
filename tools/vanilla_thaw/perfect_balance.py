"""Toggle stock THAW's two perfect-balance career flags between frames.

The original balance update retains its timers, scoring, lean and HUD. Its own
perfect-balance branch prevents balance bails; collision bails remain enabled.
The stock grind flag also covers lips. The separate skitch flag is untouched.
"""
import struct

import probe_loader as host

PINS = {
    'BalanceUpdateWitness': ('StockPerfectBalanceBranches', 0x587a00, 1491, '770ba2c18c72733052846026e213951d870d893c1c2f1650c82ec442d97e6851'),
    'BalanceCheatQuery': ('StockPerfectBalanceQuery', 0x593c10, 36, '64357ee07e8b0c3f65f278d15f5211cd2ba3a94f471394726b4306d1da5d581f'),
    'BalanceGlobalInteger': ('StockPerfectBalanceGlobalInteger', 0x417400, 40, '5c1df69111e5a94a307e5955c0527d93f95cbeebaf2b561532fae32711a1f9d6'),
    'BalanceBitQuery': ('StockPerfectBalanceBitQuery', 0x493760, 40, 'b8cf8c43356eb6dd5515ce4bc57d38476c2309f75cd1250d1849a8eedc9dcbef'),
    'BalanceBitSet': ('StockPerfectBalanceBitSet', 0x493790, 32, '84e67f5968bc017fb57a56471f8646378e415fdd42d933799b784ee3f240f35b'),
    'BalanceBitClear': ('StockPerfectBalanceBitClear', 0x4937b0, 38, '5e123bd476bf0235b116a937f747541fcdfe0c8e08b3d6d38c48445436eb5314'),
}
FLAGS = ((0xcd09e062, 187), (0xb38341c9, 191))


class PerfectBalance:
    def __init__(self, system, call, validate):
        self.system, self.call, self.validate = system, call, validate
        self.enabled = False
        self.validate()
        self.identity, flags = self._snapshot()
        for key, index in FLAGS:
            host.require(self.call('BalanceGlobalInteger', (key, 1)) == index,
                         'Original perfect-balance flag alias differs')
        host.require(all(not flags[index // 8] & (1 << (index % 8)) for _, index in FLAGS),
                     'Owned stock session already has perfect balance enabled')

    def _snapshot(self):
        s = self.system
        start, end = struct.unpack('<2I', s.read(0x84376c, 8))

        def words(pointer, count):
            host.require(pointer % 4 == 0 and 0x10000 <= start <= pointer <= end - count * 4,
                         'Original balance setting object leaves its private arena')
            return struct.unpack(f'<{count}I', s.read(pointer, count * 4))

        game = s.word(0x83e81c)
        game_words = words(game, 9)
        career = game_words[8]
        career_words = words(career, 2)
        flags = career_words[1]
        flag_words = words(flags, 4)
        host.require(game_words[0] == 0x69807c and career_words[0] == 0x698a50 and
                     flag_words[0] == 0x691d94 and flag_words[3] == 32,
                     'Original balance setting Game/Career/1024-bit flag identity differs')
        data = flag_words[2]
        raw = struct.pack('<32I', *words(data, 32))
        identity = (start, end, game, career, flags, flag_words)
        if hasattr(self, 'identity'):
            host.require(identity == self.identity, 'Original balance setting lifetime changed')
        return identity, raw

    def set_enabled(self, enabled):
        host.require(type(enabled) is bool, 'Infinite balance must be explicitly enabled or disabled')
        clock = self.validate()
        identity, before = self._snapshot()
        expected = bytearray(before)
        for _, index in FLAGS:
            mask = 1 << (index % 8)
            if enabled:
                expected[index // 8] |= mask
            else:
                expected[index // 8] &= ~mask
        if bytes(expected) == before:
            self.enabled = enabled
            return dict(infinite_balance=enabled, changed=False)
        for _, index in FLAGS:
            self.call('BalanceBitSet' if enabled else 'BalanceBitClear', (index,), identity[4])
        host.require(self._snapshot()[1] == bytes(expected),
                     'Original perfect-balance readback changed unrelated flags')
        host.require(self.validate() == clock, 'Balance setting changed the simulation clock')
        self.enabled = enabled
        return dict(infinite_balance=enabled, changed=True, original_solver=True,
                    manual=True, grind=True, lip=True, skitch=False)
