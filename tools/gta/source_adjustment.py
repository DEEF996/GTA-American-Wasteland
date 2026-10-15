"""Bound positional adjustments from the retained, completed original driver.

This is a transport allowance, not a proof of a particular collision branch.
Original velocity, clock, pose and position are never modified.
"""
import math
import struct

from geometry import thaw_to_gta


def require(ok, message):
    if not ok:
        raise ValueError(message)


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def adjustment(previous, current, micros):
    """Match MOVE3's float32 distance arithmetic, including wire quantization."""
    require(micros in (16666, 16667), 'Source adjustment requires one 60 Hz step')
    seconds = f32(float(micros) * f32(.000001))
    ordinary = f32(f32(.02) + f32(60. * seconds))
    maximum = f32(ordinary + 2.)
    distance2 = 0.
    for old, new in zip(previous, current):
        delta = f32(new - old)
        distance2 = f32(distance2 + f32(delta * delta))
    return f32(ordinary * ordinary) < distance2 <= f32(maximum * maximum)


class SourceAdjustment:
    def __init__(self, driver, context, *, origin=(0., 0., 0.), offset=0.):
        self.driver, self.context = driver, context
        self.actor = driver.actor
        require(type(self.actor) is int and self.actor == context['actor'],
                'Source adjustment actor differs from its initialized context')
        require(type(driver.frame_count) is int and driver.frame_count >= 0,
                'Source adjustment initial frame is invalid')
        require(len(origin) == 3 and all(math.isfinite(v) for v in origin) and math.isfinite(offset),
                'Source adjustment world transform is invalid')
        self.origin, self.offset = tuple(origin), offset
        self.frame = driver.frame_count
        self.count, self.previous = 0, None

    def update(self, result):
        driver = self.driver
        require(driver.started and not driver.poisoned and not driver._stepping and
                result is driver.last and driver.actor == self.actor == self.context['actor'],
                'Source adjustment requires the owned driver completed result')
        sample, clock = result['sample'], result['clock']
        frame = sample['frame']
        require(type(frame) is int and frame == self.frame + 1 == driver.frame_count and
                all(type(clock[k]) is int and clock[k] == frame for k in
                    ('native_frame_count', 'native_vblank_count', 'native_timer_frames')) and
                clock['frame_open'] is False and
                math.isfinite(clock['native_seconds_per_frame']) and
                abs(clock['native_seconds_per_frame'] - 1/60) < 1.e-12,
                'Source adjustment clock is open, discontinuous or not 60 Hz')
        require(all(type(sample[k]) is int for k in ('mode', 'state', 'walk_state', 'walking_gate')) and
                sample['mode'] in (0, 1) and 0 <= sample['state'] <= 7 and
                0 <= sample['walk_state'] <= 7 and sample['walking_gate'] == 0 and
                type(sample['bailing']) is bool,
                'Source adjustment motion context is unsupported')
        position, velocity = sample['position'], sample['velocity']
        require(len(position) == len(velocity) == 3 and
                all(math.isfinite(v) for v in (*position, *velocity)) and
                math.hypot(*velocity) * .0254 <= 60.00000005,
                'Source adjustment vectors are nonfinite or exceed the velocity limit')
        position = thaw_to_gta(position, origin=self.origin)
        position[2] += self.offset
        require(all(abs(v) <= 100000 for v in position), 'Source adjustment position exceeds the world bound')
        position = tuple(f32(v) for v in position)
        scale, next_scale, gameplay_delta = (clock[k] for k in
            ('native_time_scale', 'native_next_time_scale', 'native_gameplay_seconds_per_frame'))
        require(all(math.isfinite(v) for v in (scale, next_scale, gameplay_delta)) and
                0 <= scale <= 100 and 0 <= next_scale <= 100 and
                abs(gameplay_delta - scale * f32(1/60)) < 1.e-8,
                'Source adjustment original gameplay clock is invalid')
        count = self.count + 1
        micros = round(count * 1e6 / 60) - round(self.count * 1e6 / 60)
        # Handoff changes the native root outside driver.step; never seed from
        # driver.last or grant entry an adjustment from the preceding session.
        marked = self.previous is not None and scale == next_scale == 1 and adjustment(self.previous, position, micros)
        self.frame, self.count, self.previous = frame, count, position
        return marked
