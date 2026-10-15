"""Witness the stock bail root being reprojected at an adjacent rail node.

The original bail script can move the actor sideways while Core remains OnRail.
At the next linked node Core projects that root back onto the segment. This is
not velocity integration; only this completed, owned source transition is marked.
"""
import hashlib
import math
import struct

from probe_loader import require
from motion_contact import classify

METRES = .0254
MAX_ADJUSTMENT = 2.0
PROJECTION_PINS=(
    ('RailOvershoot',0x5e6669,87,'cd2ee7104001cd9dd7212d48889a44c4fe28719f2176d751cbd1c925a186192c'),
    ('RailNodePublication',0x5e67c1,42,'52f0f430abe44b6d97e6a1dd4d16589c1420d90db72d617821b38e2781d684fa'),
    ('RailReversePublication',0x5e66c0,168,'9b2d92fef1997cbfbacf5a7e18804f3a77949893190c5fefb4e88643fec935a8'),
    ('RailAdvanceProjection',0x5e6cda,155,'d6fefb66be1ceb7909fcfd15d5a81c9820c730e6cfc69f002c62cc7b8c5a8189'),
)


def _projection(point, start, end):
    direction = tuple(b-a for a, b in zip(start, end))
    length2 = sum(v*v for v in direction)
    require(length2 > 1., 'Correction rail segment is degenerate')
    t = sum((p-a)*d for p,a,d in zip(point,start,direction))/length2
    projected = tuple(a+min(1.,max(0.,t))*d for a,d in zip(start,direction))
    return projected, math.dist(point,projected)*METRES


def _bail_transition(previous, current, before, after, bail_board):
    if (classify(previous) != 'rail' or classify(current) != 'rail' or
            before.get('dynamic_vehicle') or after.get('dynamic_vehicle') or
            current['frame'] != previous['frame']+1 or
            previous.get('board_visible') is not False or current.get('board_visible') is not False or
            not bail_board or previous.get('detached_board_id') != bail_board or
            current.get('detached_board_id') != bail_board or
            before['rail_manager'] != after['rail_manager'] or before['source_chain'] != after['source_chain'] or
            before['rail_node'] == after['rail_node'] or
            before['rail_node'] not in after['links'] or after['rail_node'] not in before['links']):
        return False
    return True


def proves_correction(previous, current, before, after, bail_board):
    """Pure bounds around a natively witnessed adjacent-node transition."""
    if not _bail_transition(previous,current,before,after,bail_board):
        return False
    # The old/new node pair is an actual connected segment. Scripted lateral
    # bail motion stays bounded; the projection advances with native momentum.
    a,b=before['position'],after['position']
    old,old_distance=_projection(previous['position'],a,b)
    new,new_distance=_projection(current['position'],a,b)
    travel=math.dist(previous['position'],current['position'])*METRES
    return (1.02 < travel <= 1.02+MAX_ADJUSTMENT and
            old_distance <= MAX_ADJUSTMENT+.02 and new_distance <= .2 and
            math.dist(old,new)*METRES <= 1.02 and
            abs(current['position'][1]-previous['position'][1])*METRES <= .2)


def proves_corner_correction(previous, current, before, after, following, bail_board):
    """The stock forward-node transition has already entered the next segment."""
    if (not _bail_transition(previous,current,before,after,bail_board) or
            previous.get('bailing') is not True or current.get('bailing') is not True or
            following.get('dynamic_vehicle') or
            following['rail_manager'] != after['rail_manager'] or
            following['source_chain'] != after['source_chain'] or
            len(before['links']) != 2 or len(after['links']) != 2 or len(following['links']) != 2 or
            before['links'][0] != after['rail_node'] or after['links'][1] != before['rail_node'] or
            after['links'][0] != following['rail_node'] or following['links'][1] != after['rail_node'] or
            following['rail_node'] in (before['rail_node'],after['rail_node'])):
        return False
    a,b,c=before['position'],after['position'],following['position']
    old,old_distance=_projection(previous['position'],a,b)
    new,new_distance=_projection(current['position'],b,c)
    travel=math.dist(previous['position'],current['position'])*METRES
    along=(math.dist(old,b)+math.dist(b,new))*METRES
    return (1.02 < travel <= 1.02+MAX_ADJUSTMENT and
            old_distance <= MAX_ADJUSTMENT+.02 and new_distance <= .2 and along <= 1.02 and
            abs(current['position'][1]-previous['position'][1])*METRES <= .2)


def proves_native_projection(previous,current,before,after,following,bail_board,*,reverse=False):
    """Witness the stock straight-rail overshoot, including scripted bail drift."""
    if (not _bail_transition(previous,current,before,after,bail_board) or
            previous.get('bailing') is not True or current.get('bailing') is not True or
            following.get('dynamic_vehicle') or
            following['rail_manager']!=after['rail_manager'] or
            following['source_chain']!=after['source_chain'] or
            len(before['links'])!=2 or len(after['links'])!=2 or len(following['links'])!=2 or
            following['rail_node'] in (before['rail_node'],after['rail_node'])):
        return False
    if reverse:
        if (before['links'][1]!=after['rail_node'] or after['links'][0]!=before['rail_node'] or
                before['links'][0]!=following['rail_node'] or following['links'][1]!=before['rail_node']):
            return False
        # Reverse Core denotes the start of the entered segment, although the
        # original solver resets the root to its end (the previous Core node).
        ordered=(following,before,after)
    else:
        if (before['links'][0]!=after['rail_node'] or after['links'][1]!=before['rail_node'] or
                after['links'][0]!=following['rail_node'] or following['links'][1]!=after['rail_node']):
            return False
        ordered=(before,after,following)
    arrays=[previous.get(key) for key in ('position','previous_position','velocity')]
    arrays+=[current.get(key) for key in ('position','previous_position','velocity')]
    arrays+=[node['position'] for node in (before,after,following)]
    if any(not isinstance(a,(tuple,list)) or len(a)!=3 or
           not all(type(v) in (float,int) and math.isfinite(v) for v in a) for a in arrays):
        return False
    # Four float32 ULPs cover the source's distance/overshoot rounding, not a
    # positional allowance. Preserve the existing sub-inch projection ceiling.
    tolerance=min(.05,max(.002,4*max(math.ldexp(1.,math.frexp(abs(v))[1]-24)
                                   for a in arrays for v in a)))
    a,b,c=(node['position'] for node in ordered)
    lengths=math.dist(a,b),math.dist(b,c)
    if min(lengths)<=1.:return False
    directions=[tuple((q-p)/length for p,q in zip(start,end))
                for start,end,length in ((a,b,lengths[0]),(b,c,lengths[1]))]
    # This fallback is specifically the measured straight, level rail branch.
    if (math.dist(*directions)>1.e-5 or abs(directions[0][1])>1.e-5 or
            abs(directions[1][1])>1.e-5):return False
    speed=math.hypot(*previous['velocity'])
    if not 0<speed*METRES<=60:return False
    if any(math.dist(sample['velocity'],[d*speed for d in direction])>tolerance
           for sample,direction in zip((previous,current),directions)):
        return False
    # Original Core uses the distance from the old node, including the side
    # displacement from FallOffRail, before advancing onto the linked segment.
    overshoot=math.dist(previous['position'],a)-lengths[0]
    if not 0<=overshoot+speed/60<=lengths[1] or overshoot<0:return False
    projected=[b[i]+current['velocity'][i]/60+directions[1][i]*overshoot for i in range(3)]
    if math.dist(projected,current['previous_position'])>tolerance:return False
    shifts=[[sample['position'][i]-sample['previous_position'][i] for i in range(3)]
            for sample in (previous,current)]
    if (math.dist(*shifts)>tolerance or math.hypot(*shifts[1])*METRES>.2 or
            abs(shifts[1][1])>tolerance or
            abs(sum(x*d for x,d in zip(shifts[1],directions[1])))>tolerance):
        return False
    travel=math.dist(previous['position'],current['position'])*METRES
    return (1.02<travel<=1.02+MAX_ADJUSTMENT and
            abs(current['position'][1]-previous['position'][1])*METRES<=.2)


class RailCorrection:
    def __init__(self, rails, system, context):
        self.rails,self.system=rails,system
        self.context=context
        actor=context['actor']
        require(system.word(actor)==0x6989e8, 'Rail correction skater class differs')
        self.bail_board=(system.word(actor+0x18)+0x884ef81b)&0xffffffff
        self.previous=self.observation=None
        self.result=False

    def _verify_projection(self,sample,observation):
        system=self.system;actor=self.context['actor']
        core=self.context['components'][0x5bd63b29]
        require(system.word(actor)==0x6989e8 and system.word(core)==0x6a0d58 and
                system.word(core+4)==0x5bd63b29 and system.word(core+12)==actor,
                'Rail projection actor/Core ownership differs')
        for name,address,size,digest in PROJECTION_PINS:
            require(hashlib.sha256(system.read(address,size)).hexdigest()==digest,
                    'Original rail projection code differs: '+name)
        require(struct.unpack('<Q',system.read(0x8459d0,8))[0]==sample['frame'] and
                abs(struct.unpack('<f',system.read(core+0x9c,4))[0]-1/60)<1.e-8,
                'Rail projection requires the completed original 1/60 frame')
        require(system.word(core+0x3f0)==observation['rail_node'] and
                system.word(core+0x3f4)==observation['rail_manager'] and
                system.read(actor+0x68,12)==struct.pack('<3f',*sample['position']) and
                system.read(actor+0x78,12)==system.read(core+0xa8,12)==struct.pack('<3f',*sample['previous_position']),
                'Original rail projection sample/history or node differs')

    def update(self, sample):
        if self.previous is not None and sample['frame']==self.previous['frame']:
            require(sample==self.previous, 'Correction source sample changed after completion')
            return self.result
        observation=None
        if (classify(sample)=='rail' and sample.get('board_visible') is False and
                sample.get('detached_board_id')==self.bail_board):
            observation=self.rails.observe(sample)
            if observation.get('dynamic_vehicle'):
                # Owned vehicle nodes are local, not the fixed world segment.
                observation=None
            else:
                data=self.system.read(observation['rail_node'],0x60)
                # RailStream already proved group/manager/name/position ownership.
                require(struct.unpack_from('<I',data,0x44)[0]==observation['rail_name'],
                        'Correction rail node changed during the completed frame read')
                observation=dict(observation,position=struct.unpack_from('<3f',data,0x20),
                                 links=struct.unpack_from('<2I',data,0x50))
        result=bool(self.previous is not None and self.observation is not None and observation is not None and
                    proves_correction(self.previous,sample,self.observation,observation,self.bail_board))
        corner=projection=reverse=False
        if (not result and self.previous is not None and self.observation is not None and observation is not None and
                _bail_transition(self.previous,sample,self.observation,observation,self.bail_board) and
                self.previous.get('bailing') is True and sample.get('bailing') is True and
                math.dist(self.previous['position'],sample['position'])*METRES > 1.02):
            following=None
            if (self.observation['links'][0] == observation['rail_node'] and
                    observation['links'][1] == self.observation['rail_node'] and observation['links'][0]):
                following=self.rails.adjacent_node(observation,observation['links'][0])
                corner=proves_corner_correction(self.previous,sample,self.observation,observation,following,self.bail_board)
                result=corner
            elif (self.observation['links'][1] == observation['rail_node'] and
                    observation['links'][0] == self.observation['rail_node'] and self.observation['links'][0]):
                following=self.rails.adjacent_node(self.observation,self.observation['links'][0])
                reverse=True
            if not result and following is not None and proves_native_projection(
                    self.previous,sample,self.observation,observation,following,self.bail_board,reverse=reverse):
                self._verify_projection(sample,observation)
                result=projection=True
        if result:
            self.rails.log('original_rail_bail_correction',frame=sample['frame'],
                previous_position=self.previous['position'],position=sample['position'],
                previous_node=self.observation['rail_node'],node=observation['rail_node'],
                source_chain=observation['source_chain'],bail_board=self.bail_board,
                next_segment=corner,
                native_projection=projection,
                reverse_projection=projection and reverse,
                distance_metres=math.dist(self.previous['position'],sample['position'])*METRES)
        self.previous,self.observation,self.result=dict(sample),observation,result
        return result
