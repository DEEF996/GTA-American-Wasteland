"""Read completed stock model presentation without executing a renderer."""
import math
import struct

from probe_loader import require

MODEL, GEOM, INSTANCE = 0x694F04, 0x694C50, 0x695898
BOARD_PARTS = {0xa7a9d4b8, 0x04e91519, 0xe2b2dede}
PINS = {
    0x694CD0: bytes.fromhex('609f5000'),  # Stock CGeom final-matrix setter virtual.
    0x509F60: bytes.fromhex('578bf98b475085c0742e8b4c240856518d480ce878c3efff'),
    0x509F78: bytes.fromhex('8b4c24148b57508b7a4c8b742410c1e106'),
    0x40634E: bytes.fromhex('8b493c89483cc20400'),
    0x4D85E0: bytes.fromhex('8b0a8b6c24143b6908'),  # AtomicIsHidden: named part.
    0x4D85EB: bytes.fromhex('8acbbd01000000d3e5856e18'),  # Its model+18 visibility bit.
    0x4354BB: bytes.fromhex('894e5c33c93bd189564c89466c894e68'),  # Instance count/palette/scene/null owner.
    0x6A0A14: bytes.fromhex('c0cc5b00'),  # ProjectileTracker InitFromStructure.
    0x5BCCC0: bytes.fromhex('568bf18b0d04a86e0057e82137eeff8b4e0c8946188b7918c7461cffffffff33c98d90940100003b3a741b4183c21883f90a7cf3'),
    0x5BCCF4: bytes.fromhex('8b761c83c6115f8d14765ec604d001c20400'),  # Native tracked projectile activation.
    0x56E3EC: bytes.fromhex('8b4718051bf84e88'),  # Bail-board ID = skater ID + original name checksum.
}


def board_visibility(parts, mask):
    selected = [(part, bool(mask & (1 << index))) for index, part in enumerate(parts) if part in BOARD_PARTS]
    require(len(parts) <= 32 and any(part == 0xa7a9d4b8 for part, _ in selected),
            'Original model lacks its named skateboard part')
    visible = selected[0][1]
    require(all(value == visible for _, value in selected), 'Partial original board visibility is unsupported')
    return visible


class ModelFrame:
    def __init__(self, read, words, actor, component):
        self.read, self.words, self.actor, self.component = read, words, actor, component
        for address, expected in PINS.items():
            require(read(address, len(expected)) == expected, 'Original final model transform code differs')
        self.model = None

    def sample(self, palette_address, palette):
        words = self.words
        component = words(self.component, 0x20c//4)
        require(component[1] == 0x286a8d26 and component[3] == self.actor,
                'Original model component owner changed')
        model_address = component[0x208//4]
        model = words(model_address, 0x4c//4)
        require(model[0] == MODEL and 0 < model[5] <= 32 and model[0x44//4] >> 16 == model[5],
                'Original rendered model shape changed')
        head = model[4]
        if self.model != (model_address, head, model[5]):
            sentinel = words(head, 3)
            node, previous, nodes, parts, selected = sentinel[1], head, set(), [], None
            while node != head:
                require(node not in nodes and len(nodes) < model[5], 'Original rendered model list cycles')
                nodes.add(node)
                link = words(node, 3)
                require(link[2] == previous, 'Original rendered model backlink differs')
                item = words(link[0], 6)
                geom = words(item[0], 0x54//4)
                require(geom[0] == GEOM, 'Original rendered geometry class differs')
                render = words(geom[0x50//4], 0x70//4)
                require(render[0] == INSTANCE and render[0x68//4] == model_address and render[0x5c//4] == 52,
                        'Original rendered geometry ownership/count differs')
                if selected is None:
                    selected = (node, link[0], item[0], geom[0x50//4])
                parts.append(item[2])
                previous, node = node, link[1]
            require(len(nodes) == model[5] and previous == sentinel[2], 'Original rendered model list/count differs')
            self.model, self.parts, self.selected = (model_address, head, model[5]), parts, selected
            self.board_parts = set(parts) & BOARD_PARTS
        node, item, geom_address, instance = self.selected
        require(words(node, 1)[0] == item and words(item, 1)[0] == geom_address,
                'Original rendered model part changed')
        geom = words(geom_address, 0x54//4)
        render = words(instance, 0x70//4)
        require(geom[0] == GEOM and geom[0x50//4] == instance and render[0] == INSTANCE and
                render[0x68//4] == model_address and render[0x5c//4] == 52,
                'Original rendered transform ownership/count changed')
        rendered_palette = render[0x4c//4]
        if rendered_palette != palette_address:
            require(struct.pack('<832I', *words(rendered_palette, 832)) == palette,
                    'Original geometry palette differs from the completed skeleton frame')
        raw = struct.pack('<16I', *render[3:19])
        matrix = struct.unpack('<16f', raw)
        require(all(math.isfinite(v) for v in matrix), 'Nonfinite original model display matrix')
        # Source native worker is idle for this whole read; no render/task thread
        # runs between the palette and this instance's completed world transform.
        require(words(self.component + 0x208, 1)[0] == model_address,
                'Original model changed during completed frame read')
        return matrix, board_visibility(self.parts, model[0x18//4])


class DetachedBoards:
    """Read only original live board owners belonging to the controlled skater.

    Thrown-board IDs are generated by the original object system. Identity is
    proven by the native ProjectileTracker's skater pointer and matching
    skater projectile slot, rather than one observed generated object ID.
    """
    def __init__(self, words, actor, manager, attached):
        self.words, self.actor, self.manager, self.attached = words, actor, manager, attached

    def sample(self, owners, attached_visible):
        # Returning to the attached board is authoritative even while native
        # deferred cleanup retains an older composite owner for one frame.
        if attached_visible:
            return None
        words, found = self.words, None
        actor_id = words(self.actor+0x18, 1)[0]
        for owner in owners:
            if owner == self.actor:
                continue
            fields = words(owner, 0x120//4)
            require(fields[3] == self.manager, 'Detached owner left the original live manager')
            if fields[0] != 0x68f7b8 or fields[2] & 1:
                continue
            component, seen, components = fields[0x11c//4], set(), {}
            while component:
                require(component not in seen and len(seen) < 64, 'Detached component chain cycles')
                seen.add(component)
                value = words(component, 5)
                require(value[3] == owner and value[1] not in components, 'Detached component owner/type differs')
                components[value[1]] = component
                component = value[4]
            if not {0x286a8d26, 0x222756d5, 0xffb39248}.issubset(components):
                continue
            if fields[6] != (actor_id+0x884ef81b) & 0xffffffff:
                if 0x7e747547 not in components:
                    continue
                tracker = words(components[0x7e747547], 8)
                require(tracker[0] == 0x6a0a0c, 'Original projectile tracker class differs')
                if tracker[6] != self.actor:
                    continue
                require(tracker[7] < 10, 'Original projectile slot exceeds the native skater array')
                slot = words(self.actor+0x194+tracker[7]*0x18, 2)
                require(slot[0] == fields[6] and slot[1] & 0xff == 1,
                        'Detached board is not active in its original skater projectile slot')
            model_address = words(components[0x286a8d26]+0x208, 1)[0]
            model = words(model_address, 0x4c//4)
            require(model[0] == MODEL and model[2] == 52 and 0 < model[5] <= 3 and
                    model[0x44//4] >> 16 == model[5], 'Detached model shape differs')
            head = model[4]
            sentinel = words(head, 3)
            node, previous, seen, parts, presentation = sentinel[1], head, set(), [], None
            while node != head:
                require(node not in seen and len(seen) < model[5], 'Detached model list cycles')
                seen.add(node)
                link = words(node, 3)
                require(link[2] == previous, 'Detached model backlink differs')
                item = words(link[0], 6)
                require(item[2] in self.attached.board_parts and item[2] not in parts,
                        'Detached model contains foreign or duplicate board geometry')
                parts.append(item[2])
                geom = words(item[0], 0x54//4)
                require(geom[0] == GEOM, 'Detached geometry class differs')
                render = words(geom[0x50//4], 0x70//4)
                require(render[0] == INSTANCE and render[0x68//4] == 0 and render[0x5c//4] == 52 and
                        render[2] & 1 and render[0x6c//4], 'Detached copied geometry identity/count differs')
                matrix = struct.pack('<16I', *render[3:19])
                palette = struct.pack('<64I', *words(render[0x4c//4]+48*64, 64))
                if presentation is None:
                    presentation = matrix, palette
                else:
                    require(presentation == (matrix, palette), 'Detached board parts disagree about their completed frame')
                previous, node = node, link[1]
            require(len(seen) == model[5] and previous == sentinel[2], 'Detached model list/count differs')
            if not board_visibility(parts, model[0x18//4]):
                continue
            require(found is None and fields[6] != 0, 'Multiple visible detached boards or unassigned original ID')
            matrix = struct.unpack('<16f', presentation[0])
            require(all(math.isfinite(v) for v in matrix), 'Nonfinite detached board display matrix')
            found = dict(object_id=fields[6], palette=presentation[1], visual_matrix=matrix)
        return found
