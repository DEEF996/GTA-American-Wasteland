"""Persistent GTA THAW settings and the keyboard-driven native F8 panel.

Only our bounded menu mappings are writable; original trick checksums come from
the live stock catalogue. Native gameplay changes are applied by live_session.
"""
from __future__ import annotations

import copy
import ctypes as C
import json
import os
import re
from pathlib import Path
import struct
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vanilla_thaw'))
from input_mapping import GTA_BINDINGS, KNOWN_ACTIONS
from keyboard_input import SUPPORTED_KEYS, validate_bindings

LINES, LINE_BYTES, EVENTS = 24, 100, 64
HEADER = struct.Struct('<8s4I5Q2I')
PANEL_BYTES = HEADER.size + LINES * LINE_BYTES
INPUT = struct.Struct('<8s4I4Q2I64I')
assert HEADER.size == 72 and PANEL_BYTES == 2472 and INPUT.size == 320
RESERVED = {13, 41, 58, 64, 65, 83}  # J, Escape, F1, F7, F8, Num Lock.
TABS = ('Gameplay', 'Bindings', 'Tricks', 'Specials')
KEY_NAMES = {40:'Enter', 42:'Backspace', 43:'Tab', 44:'Space', 45:'-', 46:'=',
    47:'[', 48:']', 49:'Backslash', 51:';', 52:"'", 53:'`', 54:',', 55:'.', 56:'/',
    57:'Caps Lock', 73:'Insert', 74:'Home', 75:'Page Up', 76:'Delete', 77:'End',
    78:'Page Down', 79:'Right', 80:'Left', 81:'Down', 82:'Up', 83:'Num Lock',
    84:'Num /', 85:'Num *', 86:'Num -', 87:'Num +', 88:'Num Enter', 89:'Num 1',
    90:'Num 2', 91:'Num 3', 92:'Num 4', 93:'Num 5', 94:'Num 6', 95:'Num 7',
    96:'Num 8', 97:'Num 9', 98:'Num 0', 99:'Num .', 100:'Non-US Backslash',
    156:'Clear', 224:'Left Ctrl', 225:'Left Shift', 228:'Right Ctrl', 229:'Right Shift'}
KEY_NAMES.update({i:chr(ord('A')+i-4) for i in range(4, 30)})
KEY_NAMES.update({i:str((i-29)%10) for i in range(30, 40)})
KEY_NAMES.update({i:f'F{i-57}' for i in range(58, 70)})
SCANS = dict(zip((0x1e,0x30,0x2e,0x20,0x12,0x21,0x22,0x23,0x17,0x24,
    0x25,0x26,0x32,0x31,0x18,0x19,0x10,0x13,0x1f,0x14,0x16,0x2f,0x11,
    0x2d,0x15,0x2c),range(4,30)))
SCANS.update({i+2:i+30 for i in range(10)})
SCANS.update({0x0e:42,0x0f:43,0x39:44,0x0c:45,0x0d:46,0x1a:47,
    0x1b:48,0x2b:49,0x27:51,0x28:52,0x29:53,0x33:54,0x34:55,0x35:56,
    0x3a:57,0x56:100,0x2a:225,0x36:229})


def event_key(event):
    """The native ring contains VK plus its foreground physical scan code."""
    vk, scan = event & 255, (event >> 8) & 0xffff
    special = {0x0d:88 if scan & 0xff00 else 40,0x1b:41,0x24:74,0x23:77,
        0x21:75,0x22:78,0x25:80,0x26:82,0x27:79,0x28:81,0x2d:73,0x2e:76,
        0x90:83,0x6f:84,0x6a:85,0x6d:86,0x6b:87,0x6e:99,0xfe:156,
        0xa2:224,0xa3:228}
    if 0x60 <= vk <= 0x69:
        return 98 if vk == 0x60 else 88 + vk - 0x60
    if 0x70 <= vk <= 0x7b:
        return vk - 0x70 + 58
    return special.get(vk, SCANS.get(scan & 255))


def default_settings():
    return dict(schema=2, bindings=dict(GTA_BINDINGS), aerial_flips=True,
        grind_camera_lowering=False, infinite_balance=False, normal_tricks={}, special_tricks={})


def valid_binding(code):
    return type(code) is int and code in SUPPORTED_KEYS and code in KEY_NAMES


def _uint(value):
    return type(value) is int and 0 <= value <= 0xffffffff


def validate(settings, profile=None):
    if not isinstance(settings, dict) or set(settings) != set(default_settings()) or type(settings['schema']) is not int or settings['schema'] != 2:
        raise ValueError('Unsupported settings format')
    for key in ('aerial_flips', 'grind_camera_lowering', 'infinite_balance'):
        if type(settings[key]) is not bool:
            raise ValueError('Settings switches must be true or false')
    bindings = settings['bindings']
    validate_bindings(bindings)
    normal, special = settings['normal_tricks'], settings['special_tricks']
    if not isinstance(normal, dict) or not isinstance(special, dict) or len(normal)>256 or len(special)>12:
        raise ValueError('Invalid trick assignment collection')
    if any(not isinstance(k,str) or not k.isascii() or not k.isdecimal() or str(int(k))!=k or
           not _uint(int(k)) or not _uint(v) for k,v in normal.items()):
        raise ValueError('Invalid original normal trick checksum')
    if any(not isinstance(k,str) or not k.isascii() or not k.isdecimal() or str(int(k))!=k or not 0<=int(k)<12 or
           not isinstance(v,dict) or set(v)!={'trick','combo'} or not all(_uint(n) for n in v.values())
           for k,v in special.items()):
        raise ValueError('Invalid original special assignment')
    if profile is not None:
        from probe_trick_settings import validate_assignments
        baseline=dict(normal={r['checksum']:r['default_trick'] for r in profile['slots']},
            special=[(r['trick'],r['combo'],int(r.get('is_cat',False))) for r in profile['source_specials']])
        try:
            validate_assignments(baseline,profile,normal,special)
        except RuntimeError as error:
            raise ValueError(str(error)) from error
    return copy.deepcopy(settings)


def compatible(trick, combo):
    return ('Air' if trick['category'] in ('Flip','Grab','Jump') else trick['category']) == combo['category']


def save_settings(path, settings, profile=None):
    value = validate(settings, profile)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                prefix=path.name+'.',suffix='.tmp',delete=False) as output:
            temporary = Path(output.name)
            json.dump(value,output,ensure_ascii=True,indent=2)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return value


def load_settings(path):
    path = Path(path)
    if not path.exists():
        return save_settings(path, default_settings()), 'Default settings created.'
    try:
        if path.stat().st_size > 65536:
            raise ValueError('Settings file is too large')
        value = json.loads(path.read_text(encoding='utf-8'))
        if (isinstance(value, dict) and type(value.get('schema')) is int and value['schema'] == 1 and
                set(value) == set(default_settings()) - {'infinite_balance'}):
            value.update(schema=2, infinite_balance=False)
            return save_settings(path, value), 'Settings updated. Existing preferences kept.'
        return validate(value), ''
    except (ValueError, UnicodeError) as error:
        backup = path.with_name(path.name+f'.invalid-{time.time_ns()}')
        os.replace(path,backup)
        return save_settings(path,default_settings()), f'Invalid settings kept as {backup.name}. Defaults restored.'


class MenuModel:
    """Pure menu state; only explicit accepted edits reach the settings file."""
    def __init__(self, path):
        self.path = Path(path)
        self.settings, self.message = load_settings(self.path)
        self.profile = None
        self.revision = 1
        self.tab = self.row = self.choice = 0
        self.chooser = None
        self.capture = None
        self.reset_confirm = False
        self.close_requested = False

    def set_profile(self, profile):
        if profile == self.profile:
            return
        if not isinstance(profile,dict) or any(k not in profile for k in ('tricks','slots','combos','source_specials','unassigned')):
            raise ValueError('Original THAW catalogue is incomplete')
        self.profile = copy.deepcopy(profile)
        try:
            validate(self.settings,self.profile)
        except ValueError:
            # Preserve old preferences for diagnosis rather than silently discarding them.
            backup = self.path.with_name(self.path.name+f'.catalogue-mismatch-{time.time_ns()}')
            backup.write_bytes(self.path.read_bytes())
            updated = copy.deepcopy(self.settings)
            updated['normal_tricks'] = {}; updated['special_tricks'] = {}
            self.commit(updated,'Trick catalogue changed. Original assignments restored.')

    def commit(self, settings, message='Saved. Changes apply when this menu closes.'):
        try:
            value = save_settings(self.path,settings,self.profile)
        except (OSError,ValueError) as error:
            self.message = str(error); return False
        self.settings = value; self.revision += 1; self.message = message
        return True

    def rows(self):
        if self.tab == 0:
            return [('aerial_flips','Aerial flips / rolls: '+('ON' if self.settings['aerial_flips'] else 'OFF')),
                    ('grind_camera_lowering','Lower camera while grinding: '+('ON' if self.settings['grind_camera_lowering'] else 'OFF')),
                    ('infinite_balance','Infinite grind / manual / lip balance: '+('ON' if self.settings['infinite_balance'] else 'OFF')),
                    ('stats','Skater statistics: all 10 / 10'),
                    ('physics','Skating physics: original THAW'),('reset','Restore all defaults')]
        if self.tab == 1:
            return [(a, f'{"Camera (walk) / Spine transfer (skate)" if a == "CameraRecenter" else a}: '
                        + (KEY_NAMES[self.settings['bindings'][a]] if a in self.settings['bindings'] else 'Unbound'))
                    for a in sorted(KNOWN_ACTIONS)]
        if not self.profile:
            return [('loading','Original trick catalogue is loading...')]
        names = {t['checksum']:t['name'] for t in self.profile['tricks']}
        if self.tab == 2:
            return [(s,f'{combination_label(s["name"], self.settings["bindings"])}: {names.get(self.settings["normal_tricks"].get(str(s["checksum"]),s["default_trick"]),"Original")}'
                     + (' [fixed]' if s['category'] not in ('Flip','Grab','Lip','Jump') else ''))
                    for s in self.profile['slots']]
        result = []
        combo_names = {c['checksum']:combination_label(c['name'],self.settings['bindings']) for c in self.profile['combos']}
        for i, source in enumerate(self.profile['source_specials']):
            v = self.settings['special_tricks'].get(str(i),source)
            result.append(((i,'trick'),f'Slot {i+1:02}: {names.get(v["trick"],"Custom / original" if source.get("is_cat") else "Unassigned")}'
                + (' [fixed]' if source.get('is_cat') else '')))
            result.append(((i,'combo'),'    Combination: '+combo_names.get(v['combo'],'Unassigned')))
        return result

    def choose(self):
        rows = self.rows(); self.row = min(self.row,len(rows)-1)
        target = rows[self.row][0]
        if self.tab == 0:
            if target in ('aerial_flips','grind_camera_lowering','infinite_balance'):
                value = copy.deepcopy(self.settings); value[target] = not value[target]; self.commit(value)
            elif target == 'reset':
                if self.reset_confirm:
                    self.commit(default_settings(),'Default settings restored.'); self.reset_confirm = False
                else:
                    self.reset_confirm = True; self.message = 'Press Enter again to restore all defaults.'
            return
        self.reset_confirm = False
        if self.tab == 1:
            self.capture = target; self.message = 'Press a new key. Escape cancels; J and F8 are reserved.'; return
        if not self.profile:
            return
        if self.tab == 2:
            if target['category'] not in ('Flip','Grab','Lip','Jump'):
                self.message = 'This original script-controlled slot is read-only.'; return
            choices = [t for t in self.profile['tricks'] if not t['special'] and t['category']==target['category']]
            value = self.settings['normal_tricks'].get(str(target['checksum']),target['default_trick'])
        else:
            index,kind = target
            if self.profile['source_specials'][index].get('is_cat'):
                self.message='Original custom created trick slot is read-only.'; return
            source = self.settings['special_tricks'].get(str(index),self.profile['source_specials'][index])
            if kind == 'trick':
                choices = [dict(checksum=self.profile['unassigned'],name='Unassigned')] + [t for t in self.profile['tricks'] if t['special']]
            else:
                trick = next((t for t in self.profile['tricks'] if t['checksum']==source['trick']),None)
                if not trick:
                    self.message = 'Choose a special trick first.'; return
                choices = [c for c in self.profile['combos'] if compatible(trick,c)]
            value = source[kind]
        self.chooser = (target,choices)
        self.choice = next((i for i,t in enumerate(choices) if t['checksum']==value),0)

    def accept_choice(self):
        target,choices = self.chooser
        selected = choices[self.choice]['checksum']; value = copy.deepcopy(self.settings)
        if self.tab == 2:
            value['normal_tricks'][str(target['checksum'])] = selected
        else:
            index,kind = target
            entry = copy.deepcopy(value['special_tricks'].get(str(index),self.profile['source_specials'][index]))
            entry.pop('is_cat',None)
            entry[kind] = selected
            if kind == 'trick' and selected == self.profile['unassigned']:
                entry['combo'] = self.profile['unassigned']
            if kind == 'trick' and selected != self.profile['unassigned']:
                trick = next(t for t in self.profile['tricks'] if t['checksum']==selected)
                combos = [c for c in self.profile['combos'] if compatible(trick,c)]
                used = {value['special_tricks'].get(str(i),s)['combo'] for i,s in enumerate(self.profile['source_specials'])
                        if i != index and value['special_tricks'].get(str(i),s)['trick'] != self.profile['unassigned']}
                combos = [c for c in combos if c['checksum'] not in used]
                if not combos:
                    self.message='No free compatible combination. Unassign another slot first.'; return
                if entry['combo'] not in {c['checksum'] for c in combos}:
                    entry['combo']=combos[0]['checksum']
            value['special_tricks'][str(index)] = entry
        if self.commit(value):
            self.chooser = None

    def key(self, code):
        if code is None:
            return
        if self.capture:
            if code == 41:
                self.capture = None; self.message='Binding edit cancelled.'; return
            if not valid_binding(code):
                self.message='That key is reserved or unsupported. Choose another key.'; return
            conflict = next((a for a,k in self.settings['bindings'].items() if k==code and a!=self.capture),None)
            if conflict:
                self.message=f'Already assigned to {conflict}. Unbind that action first (Delete).'; return
            value=copy.deepcopy(self.settings); value['bindings'][self.capture]=code
            if self.commit(value): self.capture=None
            return
        if code == 41:
            if self.chooser: self.chooser=None
            else: self.close_requested=True
            return
        if code == 43 and not self.chooser:
            self.tab=(self.tab+1)%len(TABS); self.row=0; self.reset_confirm=False; return
        if code in (82,81,75,78,74,77):
            count=len(self.chooser[1]) if self.chooser else len(self.rows())
            old=self.choice if self.chooser else self.row
            step={82:-1,81:1,75:-12,78:12,74:-count,77:count}[code]
            new=max(0,min(count-1,old+step))
            if self.chooser: self.choice=new
            else: self.row=new
            self.reset_confirm=False; return
        if code in (79,80) and not self.chooser:
            self.tab=(self.tab+(1 if code==79 else -1))%len(TABS); self.row=0; return
        if code == 76 and self.tab == 1 and not self.chooser:
            action=self.rows()[self.row][0]; value=copy.deepcopy(self.settings)
            value['bindings'].pop(action,None); self.commit(value); return
        if code in (40,88):
            self.accept_choice() if self.chooser else self.choose()

    def lines(self):
        text=['GTA THAW - SETTINGS', '  |  '.join(('['+t+']') if i==self.tab else t for i,t in enumerate(TABS)),
              'F8: close    Tab / Left / Right: section    Enter: select',
              'Up / Down: navigate    PgUp / PgDn: page    Delete: unbind', '']
        if self.chooser:
            rows=[combination_label(c['name'],self.settings['bindings']) for c in self.chooser[1]]; current=self.choice
        else:
            rows=[r[1] for r in self.rows()]; current=self.row
        start=(current//14)*14
        for i, line in enumerate(rows[start:start+14],start):
            text.append(('> ' if i==current else '  ')+line)
        while len(text)<20: text.append('')
        text.append(f'{current+1} / {len(rows)}'+('  - Choose a replacement' if self.chooser else ''))
        text.append('Listening for a new key...' if self.capture else '')
        text.append(self.message)
        text.append('Saved locally. J switches THAW mode after closing this panel.')
        return text


def combination_label(value, bindings):
    match = re.fullmatch(r'(SpAir|SpGrind|SpLip|SpMan(?:ual)?|Air|Lip)_(.+)', str(value), re.IGNORECASE)
    if not match:
        return slot_label(value)
    context, combination = match.groups()
    actions = {'u':'Forward','d':'Backward','l':'Left','r':'Right',
               'square':'Flip','circle':'Grab','triangle':'Grind'}
    def key(token):
        action = actions.get(token.lower())
        return KEY_NAMES.get(bindings.get(action), action or token)
    simultaneous = re.fullmatch(r'(Square|Circle|Triangle)([UDLR]{1,2})', combination, re.IGNORECASE)
    if simultaneous:
        button, direction = simultaneous.groups()
        label = ' + '.join([*(key(d) for d in direction), key(button)])
    else:
        label = ', '.join(key(part) for part in combination.split('_'))
    context = 'Manual' if context.lower() in ('spman', 'spmanual') else context.removeprefix('Sp')
    return context + ': ' + label


def slot_label(value):
    text=str(value).replace('_',' ')
    for original,label in (('square','Flip'),('circle','Grab'),('triangle','Grind')):
        text=re.sub(original,label,text,flags=re.IGNORECASE)
    return text


class SettingsMenu(MenuModel):
    def __init__(self, observer, path, profile=None):
        super().__init__(path)
        from preview_pose import Publisher
        self.observer=observer; self.transport=None; self.mapping=None; self.view=None
        self.open=False; self.generation=0; self.last_event=0; self.sequence=0
        self.request=max(1,observer.api.GetTickCount64())
        try:
            self.transport=Publisher(observer,transport='MENU_V1',packet_size=PANEL_BYTES)
            self.mapping=observer.api.OpenFileMappingW(4,False,f'Local\\THAW_GTA_MENU_INPUT_V1_{observer.pid}')
            if not self.mapping: raise C.WinError(C.get_last_error())
            self.view=observer.api.MapViewOfFile(self.mapping,4,0,0,INPUT.size+8)
            if not self.view: raise C.WinError(C.get_last_error())
            if profile is not None: self.set_profile(profile)
            self.publish()
        except BaseException:
            self.close(); raise

    def poll(self):
        from native.inspect_state import read_mapping_body, SnapshotBusy
        try:
            raw=read_mapping_body(self.view,INPUT.size,self.observer.api,self.observer.process,'settings input')
        except SnapshotBusy:
            self.publish(); return self.open
        data=INPUT.unpack(raw)
        magic,version,size,pid,opened,created,sequence,tick,generation,event_count,reserved,*events=data
        now=self.observer.api.GetTickCount64()
        if (magic,version,size,pid,created)!=(b'GTAMIN01',1,INPUT.size,self.observer.pid,self.observer.created) or \
                opened>1 or reserved or not sequence or tick>now or generation<self.generation:
            raise ValueError('Invalid native menu input lifetime or freshness')
        if now-tick>1500:
            # GTA stops producing input snapshots while minimized or paused.
            self.open=False; self.last_event=event_count
            self.publish()
            return False
        if generation != self.generation:
            self.generation=generation; self.open=bool(opened); self.close_requested=False
            self.capture=None; self.chooser=None; self.reset_confirm=False
        self.open=bool(opened) and not self.close_requested
        if event_count<self.last_event or event_count-self.last_event>EVENTS:
            self.message='Input queue reset. Please repeat the last menu key.'
            self.last_event=event_count
        while self.last_event<event_count:
            event=events[self.last_event%EVENTS]; self.last_event+=1
            if self.open: self.key(event_key(event))
        if self.close_requested: self.open=False
        self.publish()
        return self.open

    def publish(self):
        if not self.transport: return
        self.sequence+=1
        lines=self.lines()
        encoded=b''.join(line.encode('ascii','replace')[:LINE_BYTES-1].ljust(LINE_BYTES,b'\0') for line in lines)
        if len(encoded)!=LINES*LINE_BYTES: raise ValueError('Invalid settings panel line count')
        body=HEADER.pack(b'GTAMENU1',1,PANEL_BYTES,self.observer.pid,int(self.close_requested),
            self.observer.created,self.request,self.sequence,self.observer.api.GetTickCount64(),self.generation,
            LINES,0)+encoded
        self.transport.publish(body,self.sequence)

    def close(self):
        if self.transport: self.transport.close(); self.transport=None
        if self.view: self.observer.api.UnmapViewOfFile(self.view); self.view=None
        if self.mapping: self.observer.api.CloseHandle(self.mapping); self.mapping=None
