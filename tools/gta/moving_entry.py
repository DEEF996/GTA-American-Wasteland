"""Two-phase ground/known-jump entry; no source frames advance in preparation.

One MOVE3 marker asks the game thread to capture/hold the actual CJ root and
velocity for at most500ms. Only the matching captured state can commit sample00.
No background publisher, zero-velocity replacement or deadline renewal.
"""
import math
import struct
import time

import preview_gameplay_move as movement

OWNED, PREPARING = 64, 256
PREPARE_MS = 500


class EntryUnavailable(ValueError):
    """Normal game-thread capture/ownership race; no original engine failure."""


def require_entry(ok, message):
    if not ok:
        raise EntryUnavailable(message)


def entry_scene(state):
    require_entry(state['control_eligible'] and state['flags'] & 8 and 0 <= state['age_ms'] <= 100,
                  'Entry needs fresh foreground, live on-foot GTA')
    return movement.scene(state)


def sample(state, contact=movement.GROUND):
    return dict(source_tick=0, source_time_us=0, position=list(state['position']),
                velocity=list(state['raw_gta_velocity']), heading=state['heading'], contact=contact,
                **dict.fromkeys(movement.SOURCE_FIELDS, 0))


def contact(state):
    bits=state.get('ped_motion_flags',0)
    if state.get('grounded') and bits==1: return movement.GROUND
    if not state.get('grounded') and bits==2: return movement.AIR
    raise EntryUnavailable('Entry needs unambiguous ground or airborne contact, not landing')


def prepare_packet(state, identity, request, now_ms, duration_ms, generation, *, allow_air=False):
    mode=contact(state)
    require_entry(mode==movement.GROUND or allow_air, 'Air entry needs prior ground calibration for this actor/scene')
    # Reuse every existing identity/time/finite/speed guard. Contact0 is an
    # interactive preparation marker only, not a simulated contact classification.
    raw = bytearray(movement.packet(state,identity,request,1,now_ms,duration_ms,
                                   sample(state),generation,interactive=True))
    struct.pack_into('<I',raw,100,0 if mode==movement.GROUND else 3)
    return bytes(raw)


def unchanged(state, captured):
    return all(state[key] == captured[key] for key in ('position','heading','raw_gta_velocity'))


def check(state, entry, *, preparing=True):
    require_entry(entry_scene(state) == entry['identity'] and
                     state['f7_requests'] == entry['f7_requests'], 'Scene or F7 changed during entry')
    require_entry(state['flags'] & OWNED and bool(state['flags'] & PREPARING) == preparing,
                     'GTA released or changed entry ownership')
    require_entry(unchanged(state,entry['captured']), 'Captured GTA entry was changed by another owner')
    require_entry(contact(state)==entry['contact'], 'Captured entry contact changed')


def begin(observer, publisher, duration_ms, generation=1, *, calibration=None, clock=time.monotonic, sleep=time.sleep):
    """Publish exactly once, then wait for a fresh game-thread capture acknowledgement."""
    initial = observer.read()
    identity = entry_scene(initial)
    require_entry(not initial['flags'] & (OWNED|PREPARING), 'Another movement lease is already active')
    now_ms = observer.api.GetTickCount64()
    entry = dict(identity=identity, request=now_ms, sent_ms=now_ms,
                 duration_ms=duration_ms, generation=generation, f7_requests=initial['f7_requests'], contact=contact(initial))
    try:
        offset=calibration.get('offset') if calibration is not None else None
        allow_air=(calibration is not None and calibration.get('identity')==identity and
                   isinstance(offset,(int,float)) and math.isfinite(offset) and .5<offset<1.5)
        publisher.publish(prepare_packet(initial,identity,now_ms,now_ms,duration_ms,generation,allow_air=allow_air),1)
        until = clock()+.1
        while True:
            state = observer.read()
            require_entry(entry_scene(state) == identity and
                             state['f7_requests'] == entry['f7_requests'], 'Scene or F7 changed during preparation')
            require_entry(clock() < until, 'GTA did not acknowledge the entry preparation')
            if state['flags'] & OWNED:
                require_entry(state['flags'] & PREPARING and state['sequence'] > initial['sequence'],
                                 'GTA did not publish a fresh preparation capture')
                require_entry(contact(state)==entry['contact'] and
                                 math.hypot(*state['raw_gta_velocity']) <= 1.200000001,
                                 'Captured GTA entry exceeds supported motion/contact')
                entry['captured'] = state
                return entry
            sleep(.001)
    except BaseException:
        publisher.clear()
        raise


def commit(observer, publisher, entry, *, clock=time.monotonic, sleep=time.sleep):
    """Call after the original handoff. A source failure must clear, never commit."""
    try:
        state = observer.read()
        check(state,entry)
        now_ms = observer.api.GetTickCount64()
        require_entry(0 <= now_ms-entry['sent_ms'] < PREPARE_MS,
                         'Original source handoff exceeded the preparation deadline')
        publisher.publish(movement.packet(state,entry['identity'],entry['request'],2,now_ms,
            entry['duration_ms'],sample(entry['captured'],entry['contact']),entry['generation'],interactive=True),2)
        until = clock()+.08
        while True:
            state = observer.read()
            require_entry(clock() < until, 'GTA did not commit the captured entry')
            require_entry(0 <= observer.api.GetTickCount64()-entry['sent_ms'] < PREPARE_MS,
                             'GTA preparation deadline expired before commit acknowledgement')
            if not state['flags'] & PREPARING:
                check(state,entry,preparing=False)
                return state
            check(state,entry)
            sleep(.001)
    except BaseException:
        publisher.clear()
        raise
