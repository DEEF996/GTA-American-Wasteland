"""Contact classification of a completed original FrameDriver sample.

State::Walking 5F2440 and State::Skating 5F2460 both require +130 == 0;
State+1C then selects walking (1) or skating (0). The legacy sample name
``walking_gate`` is the Driving flag (5F2480), not an off-board or bail flag.
For walking, original Walk_Ground 5F23B0 / Walk_Air 5F23C0 read Walk+18,
not the inactive Core state. Predicates 5F2420 / 5F2410 also expose states 5 / 7;
their member names are unidentified, so labels retain the exact enum number.
Adapters transport the original pose and velocity without replacing physics.
"""
from collections.abc import Mapping

SKATING_CONTACTS = ('ground', 'air', 'wall', 'lip', 'rail', 'wallplant', 'stall', 'tiretap')
WALKING_CONTACTS = {0: 'ground', 1: 'air', 2: 'wall', 3: 'hang', 4: 'ladder',
                    5: 'walk_state_5', 6: 'anim_wait', 7: 'walk_state_7'}


def classify(sample):
    """Return the named original contact of the currently active physics owner."""
    if not isinstance(sample, Mapping):
        raise ValueError('A completed original FrameDriver sample is required')
    mode, inhibited = sample.get('mode'), sample.get('walking_gate')
    if type(mode) is not int or mode not in (0, 1) or type(inhibited) is not int or inhibited != 0:
        raise ValueError('Original Walking/Skating predicate is false or mode is unsupported')
    state = sample.get('walk_state' if mode else 'state')
    if type(state) is not int:
        raise ValueError('Original active physics state is not an integer')
    if mode == 0 and 0 <= state < len(SKATING_CONTACTS):
        return SKATING_CONTACTS[state]
    if mode == 1 and state in WALKING_CONTACTS:
        return WALKING_CONTACTS[state]
    raise ValueError('Original active physics state has no verified contact predicate')
