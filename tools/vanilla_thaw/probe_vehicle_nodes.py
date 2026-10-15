"""Authored local vehicle rails/hooks; original THAW parses all native objects."""
import math
import struct

from assets_export import qbkey, require
from carentan_qb import node_fields
import rail_export


def compile_nodes(basename, rails, hooks):
    basename = rail_export._basename(basename)
    require(len(rails) <= 2048 and len(hooks) in (0, 2) and bool(rails or hooks),
            'Vehicle local node bounds differ')
    nodes = []
    for row in rails:
        require((row['thaw_type'], row['thaw_terrain']) in rail_export.terrain_mapping.RAIL_PAIRS,
                'Unknown vehicle rail terrain')
        following = row['next_index']
        require(following is None or type(following) is int and 0 <= following < len(rails),
                'Vehicle rail link leaves its local shape')
        nodes.append(dict(row, kind='RailNode'))
    for index, position in enumerate(hooks):
        nodes.append(dict(name=qbkey(f'{basename}_hook_{index}'), position_thaw=position, kind='ObjectHook'))
    require(len({row['name'] for row in nodes}) == len(nodes), 'Duplicate vehicle node names')
    count = len(nodes)
    owner = qbkey(f'models/{basename}.qb')
    out = bytearray(struct.pack('<2I', 0, 0) + rail_export.HEADER)
    out.extend(struct.pack('<5I3I', 0xc0400, qbkey(basename+'_NodeArray'), owner, 48, 0, 0xa0100, count, 60))
    out.extend(bytes(count*4))
    for index, row in enumerate(nodes):
        position = row['position_thaw']
        require(len(position) == 3 and all(math.isfinite(v) and abs(v) <= 100000 for v in position),
                'Invalid local vehicle node position')
        struct.pack_into('<I', out, 60+index*4, len(out))
        out.extend(struct.pack('<2I', 0x10000, len(out)+8))
        fields = [(0x1b00, qbkey('Name'), row['name']), (0xd00, qbkey('Pos'), position),
                  (0xd00, qbkey('Angles'), (0., 0., 0.)),
                  (0x1b00, qbkey('Class'), qbkey(row['kind'])),
                  (0x1b00, 0, qbkey('CreatedAtStart'))]
        if row['kind'] == 'RailNode':
            fields.extend((0x1b00, qbkey(key), qbkey(row[value]))
                          for key, value in (('Type', 'thaw_type'), ('TerrainType', 'thaw_terrain')))
            if row['next_index'] is not None:
                fields.append((0x1900, qbkey('Links'), row['next_index']))
        previous = None
        for kind, key, value in fields:
            at = len(out)
            if previous is not None:
                struct.pack_into('<I', out, previous+12, at)
            previous = at
            out.extend(struct.pack('<4I', kind, key, value if kind == 0x1b00 else at+16, 0))
            if kind == 0xd00:
                out.extend(struct.pack('<I3f', 0x100, *value))
            elif kind == 0x1900:
                out.extend(struct.pack('<3I', 0x10100, 1, value))
    order = sorted(range(count), key=lambda i: nodes[i]['name'])
    for suffix, kind, values in (('_NodeArray_SortedNames', 0xd0100, [nodes[i]['name'] for i in order]),
                                 ('_NodeArray_SortedIndices', 0x10100, order)):
        at = len(out)
        out.extend(struct.pack('<5I3I', 0xc0400, qbkey(basename+suffix), owner, at+20, 0, kind, count, at+32))
        out.extend(struct.pack('<'+str(count)+'I', *values))
    struct.pack_into('<I', out, 4, len(out))
    for index, row in enumerate(nodes):
        fields = node_fields(out, struct.unpack_from('<I', out, 60+index*4)[0])
        require(fields[qbkey('Name')] == (0x1b00, row['name']) and
                fields[qbkey('Class')] == (0x1b00, qbkey(row['kind'])) and
                fields[0] == (0x1b00, qbkey('CreatedAtStart')), 'Vehicle QB roundtrip differs')
    return bytes(out)
