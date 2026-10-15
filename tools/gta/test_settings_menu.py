import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import settings_menu as menu
from probe_trick_settings import UNASSIGNED


def catalogue():
    return dict(unassigned=UNASSIGNED,
        tricks=[dict(checksum=1,name='Kickflip',category='Flip',special=False),
                dict(checksum=2,name='Heelflip',category='Flip',special=False),
                dict(checksum=3,name='Indy',category='Grab',special=False),
                dict(checksum=4,name='Special flip',category='Flip',special=True),
                dict(checksum=5,name='Special grind',category='Grind',special=True)],
        slots=[dict(checksum=100,name='Air_SquareL',category='Flip',default_trick=1),
               dict(checksum=101,name='extra_square',category='Extra',default_trick=999)],
        combos=[dict(checksum=200,name='Down_Up_Grab',category='Air'),
                dict(checksum=201,name='Up_Down_Grind',category='Grind')],
        source_specials=[dict(trick=4,combo=200,is_cat=False),
                         dict(trick=UNASSIGNED,combo=UNASSIGNED,is_cat=False)])


class SettingsTests(unittest.TestCase):
    def test_authored_combinations_display_current_bindings(self):
        bindings=menu.default_settings()['bindings']
        self.assertEqual(menu.combination_label('Air_SquareDL', bindings), 'Air: S + A + Num 4')
        self.assertEqual(menu.combination_label('SpAir_D_L_Circle', bindings), 'Air: S, A, Num 6')
        for prefix in ('SpMan', 'SpManual'):
            self.assertEqual(menu.combination_label(prefix+'_D_U_Triangle', bindings), 'Manual: S, W, Num 8')
        bindings['Grab']=6  # Rebound to C.
        self.assertEqual(menu.combination_label('SpAir_D_L_Circle', bindings), 'Air: S, A, C')
        bindings['Grind']=9  # Rebound to F.
        for prefix in ('SpMan', 'SpManual'):
            self.assertEqual(menu.combination_label(prefix+'_D_U_Triangle', bindings), 'Manual: S, W, F')
        self.assertEqual(menu.combination_label('Kickflip', bindings), 'Kickflip')

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'settings.json'

    def test_first_run_defaults_and_corrupt_backup(self):
        settings,message=menu.load_settings(self.path)
        self.assertTrue(settings['aerial_flips']); self.assertFalse(settings['grind_camera_lowering'])
        self.assertFalse(settings['infinite_balance'])
        self.assertEqual(settings['bindings']['CameraRecenter'],44)
        self.path.write_text('{bad json')
        recovered,_=menu.load_settings(self.path)
        self.assertEqual(recovered,settings)
        backups=list(self.path.parent.glob('*.invalid-*'))
        self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_text(),'{bad json')

    def test_failed_atomic_replace_preserves_previous_settings(self):
        before=menu.save_settings(self.path,menu.default_settings())
        changed=copy.deepcopy(before); changed['aerial_flips']=False
        with patch.object(menu.os,'replace',side_effect=OSError('Disk denied')):
            with self.assertRaises(OSError): menu.save_settings(self.path,changed)
        self.assertEqual(json.loads(self.path.read_text()),before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_reserved_conflicting_and_malformed_bindings(self):
        for code in (13,41,58,64,65,False,500):
            with self.subTest(code=code):
                value=menu.default_settings(); value['bindings']['Ollie']=code
                with self.assertRaises(ValueError): menu.validate(value)
        value=menu.default_settings(); value['bindings']['Ollie']=26
        with self.assertRaises(ValueError): menu.validate(value)
        value=menu.default_settings(); value['aerial_flips']='false'
        with self.assertRaises(ValueError): menu.validate(value)

    def test_bind_edit_conflict_unbind_and_restart_persistence(self):
        model=menu.MenuModel(self.path); model.tab=1
        model.row=next(i for i,r in enumerate(model.rows()) if r[0]=='Ollie')
        model.key(40); model.key(26)
        self.assertEqual(model.capture,'Ollie'); self.assertIn('Forward',model.message)
        model.key(21)  # R: previously unassigned.
        self.assertIsNone(model.capture)
        self.assertEqual(menu.MenuModel(self.path).settings['bindings']['Ollie'],21)
        model.key(76)
        self.assertNotIn('Ollie',model.settings['bindings'])

    def test_contextual_space_label_keeps_saved_binding_and_rebinds_both_actions(self):
        from input_mapping import map_keys
        import struct
        model=menu.MenuModel(self.path); model.tab=1
        label='Camera (walk) / Spine transfer (skate): '
        self.assertEqual(dict(model.rows())['CameraRecenter'],label+'Space')
        model.row=next(i for i,row in enumerate(model.rows()) if row[0]=='CameraRecenter')
        model.key(40); model.key(21)  # Rebind the existing setting to R.
        restored=menu.MenuModel(self.path); restored.tab=1
        self.assertEqual(dict(restored.rows())['CameraRecenter'],label+'R')
        bindings=restored.settings['bindings']
        self.assertEqual(bindings['CameraRecenter'],21)
        self.assertNotIn('SpineTransfer',bindings)
        self.assertFalse(map_keys({44},bindings,skating=False).recenter_camera)
        walking=map_keys({21},bindings,skating=False)
        skating=map_keys({21},bindings,skating=True)
        self.assertTrue(walking.recenter_camera); self.assertFalse(skating.recenter_camera)
        self.assertEqual(struct.unpack_from('<I',walking.packet,0x24)[0],0)
        self.assertEqual(struct.unpack_from('<I',skating.packet,0x24)[0],0x0c)
        restored.row=next(i for i,row in enumerate(restored.rows()) if row[0]=='CameraRecenter')
        restored.key(76)
        self.assertEqual(dict(restored.rows())['CameraRecenter'],label+'Unbound')
        self.assertEqual(struct.unpack_from('<I',map_keys({21},restored.settings['bindings'],skating=True).packet,0x24)[0],0)

    def test_normal_category_and_readonly_slot(self):
        profile=catalogue(); settings=menu.default_settings()
        settings['normal_tricks']={'100':2}; menu.validate(settings,profile)
        settings['normal_tricks']={'100':3}
        with self.assertRaises(ValueError): menu.validate(settings,profile)
        settings['normal_tricks']={'101':2}
        with self.assertRaises(ValueError): menu.validate(settings,profile)
        model=menu.MenuModel(self.path); model.set_profile(profile); model.tab=2; model.row=1; model.key(40)
        self.assertIsNone(model.chooser); self.assertIn('read-only',model.message)

    def test_special_category_combo_conflict_and_clear(self):
        model=menu.MenuModel(self.path); model.set_profile(catalogue()); model.tab=3; model.row=2
        model.key(40)
        model.choice=next(i for i,r in enumerate(model.chooser[1]) if r['checksum']==5)
        model.key(40)
        self.assertEqual(model.settings['special_tricks']['1'],dict(trick=5,combo=201))
        model.row=0; model.key(40); model.choice=0; model.key(40)
        self.assertEqual(model.settings['special_tricks']['0'],dict(trick=UNASSIGNED,combo=UNASSIGNED))
        value=menu.default_settings(); value['special_tricks']={'1':dict(trick=4,combo=200)}
        with self.assertRaises(ValueError): menu.validate(value,catalogue())

    def test_cat_baseline_preserved_and_readonly(self):
        profile=catalogue(); profile['source_specials'][1]=dict(trick=999,combo=201,is_cat=True)
        menu.validate(menu.default_settings(),profile)
        model=menu.MenuModel(self.path); model.set_profile(profile); model.tab=3; model.row=2
        model.key(40); self.assertIsNone(model.chooser); self.assertIn('read-only',model.message)

    def test_default_reset_requires_second_enter(self):
        model=menu.MenuModel(self.path); model.key(40)
        self.assertFalse(model.settings['aerial_flips'])
        model.row=next(i for i,r in enumerate(model.rows()) if r[0]=='reset'); model.key(40)
        self.assertFalse(model.settings['aerial_flips'])
        model.key(40); self.assertTrue(model.settings['aerial_flips'])

    def test_balance_toggle_persists_and_schema_one_migrates_without_losing_preferences(self):
        legacy=menu.default_settings(); legacy['schema']=1; del legacy['infinite_balance']
        legacy['bindings']['Ollie']=21
        legacy['normal_tricks']={'100':2}; legacy['aerial_flips']=False
        self.path.write_text(json.dumps(legacy))
        model=menu.MenuModel(self.path)
        self.assertEqual(model.settings,{**legacy,'schema':2,'infinite_balance':False})
        self.assertEqual(json.loads(self.path.read_text()),model.settings)
        model.row=next(i for i,r in enumerate(model.rows()) if r[0]=='infinite_balance')
        model.key(40)
        self.assertTrue(menu.MenuModel(self.path).settings['infinite_balance'])
        model.key(40)
        self.assertFalse(menu.MenuModel(self.path).settings['infinite_balance'])
        self.assertEqual(list(self.path.parent.glob('*.invalid-*')),[])

    def test_balance_setting_requires_boolean(self):
        for invalid in (1,0,'true',None):
            value=menu.default_settings(); value['infinite_balance']=invalid
            with self.assertRaises(ValueError): menu.validate(value)

    def test_panel_bounds_and_physical_events(self):
        model=menu.MenuModel(self.path)
        for tab in range(4):
            model.tab=tab
            self.assertEqual(len(model.lines()),menu.LINES)
        self.assertEqual(menu.event_key(ord('Z')|(0x15<<8)),28)  # Physical Y, foreign layout.
        self.assertEqual(menu.event_key(0x66|(0x4d<<8)),94)
        self.assertEqual(menu.event_key(0xa3|(0xe01d<<8)),228)
        self.assertEqual(menu.event_key(0x77),65)
        self.assertEqual(menu.PANEL_BYTES,2472)


if __name__=='__main__': unittest.main()
