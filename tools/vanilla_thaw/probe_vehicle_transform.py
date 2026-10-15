"""One worker command for the two unchanged original collider setters."""
import hashlib
import struct

import probe_loader as host


def transform_stub(position_function, orientation_function, owner, collider, record):
    pack = lambda value: struct.pack('<I', value)
    host.require(all(type(v) is int and 0x10000 <= v <= 0xfffffe00 and not v % 4
                     for v in (position_function, orientation_function, owner, collider, record)),
                 'Vehicle transform requires aligned owned x86 addresses')
    code = bytearray(b'\xc7\x05' + pack(record) + pack(2))
    for stage, function, argument in ((1, position_function, owner+0x68),
                                      (2, orientation_function, owner+0x88)):
        code += b'\xc7\x05' + pack(record+68) + pack(stage)
        code += b'\x68' + pack(argument) + b'\xb9' + pack(collider)
        code += b'\xb8' + pack(function) + b'\xff\xd0'
    code += b'\xc7\x05' + pack(record) + pack(6) + host.STUB
    return bytes(code)


def apply(world, prefix, owner, collider):
    """Preserve setter order, pins, deadlines and failure of the whole update."""
    host.require(prefix in ('Mesh', 'Group'), 'Unknown vehicle collider kind')
    worker = world.system.worker
    worker.check_call_access()
    pins = [world.pins[prefix+name] for name in ('SetPosition', 'SetOrientation')]
    for name, address, size, digest in pins:
        host.require(hashlib.sha256(world.read(address, size)).hexdigest() == digest,
                     'Pinned vehicle transform entry differs: '+name)
    code = transform_stub(pins[0][1], pins[1][1], owner, collider, world.record)
    world.write(world.record+36, bytes(36))
    world.write(world.record+452, bytes(24))
    address = worker.prepare_command(code)
    try:
        waited, returned = worker.execute(address, world.deadline)
        values = struct.unpack('<18I', world.read(world.record, 72))
        if waited != 0 or returned != 0 or values[0] != 6:
            diagnostic = dict(function=prefix+'Transform', stage=values[17],
                              thread_wait=waited, worker_return=returned,
                              status=values[0], exception_code=values[9], eip=values[10])
            try:
                diagnostic.update(host.read_fault_stack(world.api, world.process, world.record))
                if waited == 258:
                    from probe_worker_timeout import collect
                    diagnostic['owned_thread_timeout'] = collect(world.api, world.process, worker)
            except (OSError, ValueError, RuntimeError) as error:
                diagnostic['capture_error'] = str(error)
            world.log('native_fault_diagnostics', **diagnostic)
            raise RuntimeError(f'Original {prefix} transform failed: status={values[0]} '
                               f'EIP={values[10]:#x} wait={waited} stage={values[17]}')
        host.require(world.read(address, len(code)) == code, 'Owned vehicle transform glue changed')
        for name, target, size, digest in pins:
            host.require(hashlib.sha256(world.read(target, size)).hexdigest() == digest,
                         'Original vehicle transform changed: '+name)
    except BaseException:
        world.poisoned = True
        raise
