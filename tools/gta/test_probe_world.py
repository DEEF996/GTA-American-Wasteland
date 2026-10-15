"""Read-only live query guards, tested without launching GTA or touching memory."""
from copy import deepcopy
from pathlib import Path
import unittest

import probe_world as p


class FakeObserver:
    pid = 321
    created = 987654321
    exe = Path.cwd() / "fixture-game" / "gta_sa.exe"

    def __init__(self, *states):
        self.states = iter(states)

    def read(self):
        state = next(self.states)
        if isinstance(state, Exception):
            raise state
        return deepcopy(state)


def snapshot(**overrides):
    state = dict(protocol="GTATHAW2", pid=321, creation_100ns=987654321,
                 capabilities=3, profile_path=str(FakeObserver.exe.parent / "thaw_gta" / "profile"),
                 age_ms=20, flags=p.PED_PRESENT | p.ON_FOOT, ped_address=0x123400,
                 epoch=3, sequence=100, area=13, ped_area=13,
                 position=[1, 2, 3], raw_gta_velocity=[0, 0, 0], heading=0,
                 health=100, time_step=1, game_timer=1000, frame_counter=25,
                 clock_hour=7, clock_minute=30, clock_second=15,
                 controls_disabled_mask=0, cutscene_flags=0, ped_motion_flags=1,
                 control_eligible=True, grounded=True)
    state.update(overrides)
    return state


class ProbeTests(unittest.TestCase):
    def lookup(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"stats": {"instances": 3, "models": 2, "primitives": 45},
                "omitted": {"requires_live_dynamic_transform": 1}}

    def setUp(self):
        self.calls = []

    def run_once(self, before, after=None, clock=lambda: 0):
        observer = FakeObserver(before, after if after is not None else before)
        return p.probe_once(observer, Path("fixture.db"), 128, query=self.lookup, clock=clock)

    def test_lookup_stamps_scene_and_excludes_dynamic_and_conditional(self):
        result = self.run_once(snapshot(), snapshot(sequence=101))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["physics_ready"])
        self.assertEqual(result["scene"]["area"], 13)
        self.assertEqual(result["sequence_end"], 101)
        args, kwargs = self.calls[0]
        self.assertEqual(args, (Path("fixture.db"), [1, 2, 3], 128))
        self.assertEqual(kwargs, dict(interior=13, active_streams=(), hour=7.,
                                     include_dynamic=False, max_instances=10000,
                                     max_primitives=250000))
        self.assertEqual(result["clock_before"], [7, 30, 15])
        self.assertEqual(result["clock_after"], [7, 30, 15])
        self.assertTrue(result["grounded"])
        self.assertEqual(result["counts"]["primitives"], 45)
        self.assertNotIn("models", result)

    def test_scene_change_discards_query_counts(self):
        for change in (dict(epoch=4), dict(ped_address=0x123500), dict(area=1, ped_area=1)):
            with self.subTest(change=change):
                result = self.run_once(snapshot(), snapshot(**change))
                self.assertFalse(result["accepted"])
                self.assertEqual(result["reason"], "scene_changed_during_query")
                self.assertNotIn("counts", result)

    def test_no_ped_pause_fault_vehicle_and_wrong_profile_never_query(self):
        cases = [(dict(flags=0, ped_address=0), "no_ped"),
                 (dict(flags=19), "paused"), (dict(flags=49), "native_read_fault"),
                 (dict(flags=5), "not_on_foot"), (dict(ped_area=1), "area_transition"),
                 (dict(profile_path=str(FakeObserver.exe.parent / "ordinary-profile")), "wrong_isolated_profile")]
        for change, reason in cases:
            with self.subTest(reason=reason):
                result = self.run_once(snapshot(**change))
                self.assertEqual(result["reason"], reason)
        self.assertEqual(self.calls, [])

    def test_stale_input_and_expired_result_are_rejected(self):
        result = self.run_once(snapshot(age_ms=1001))
        self.assertEqual(result["reason"], "stale_snapshot")
        self.assertEqual(self.calls, [])
        result = self.run_once(snapshot(), snapshot(age_ms=1001))
        self.assertEqual(result["reason"], "stale_snapshot")
        self.assertNotIn("counts", result)
        ticks = iter((0, 0, .7, 1.1))
        result = self.run_once(snapshot(), clock=lambda: next(ticks))
        self.assertEqual(result["reason"], "query_snapshot_expired")

    def test_wrong_owner_nonfinite_and_outside_bounds_never_query(self):
        for change in (dict(pid=322), dict(creation_100ns=1),
                       dict(position=[float("nan"), 0, 0]), dict(position=[100001, 0, 0])):
            with self.subTest(change=change):
                self.assertFalse(self.run_once(snapshot(**change))["accepted"])
        self.assertEqual(self.calls, [])

    def test_clock_rollback_and_leaving_region_discard_result(self):
        result = self.run_once(snapshot(), snapshot(sequence=99))
        self.assertEqual(result["reason"], "snapshot_clock_rolled_back")
        result = self.run_once(snapshot(), snapshot(position=[100, 0, 0]))
        self.assertEqual(result["reason"], "player_left_query_margin")

    def test_bounded_options_and_summary_never_claim_physics(self):
        for seconds, radius in ((61, 128), (float("nan"), 128), (1, float("inf")), (1, 513), (-1, 1)):
            with self.assertRaises(ValueError):
                p.validate_options(1, seconds, radius)
        with self.assertRaises(ValueError):
            p.validate_options(0, 1, 128)
        samples = [self.run_once(snapshot()), self.run_once(snapshot(flags=0, ped_address=0))]
        summary = p.summarize(samples)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["rejections"], {"no_ped": 1})
        self.assertEqual(summary["areas"], [13])
        self.assertEqual(summary["clock_hours"], [7])
        self.assertFalse(summary["physics_ready"])

    def test_cutscene_and_disabled_controls_block_before_and_after_query(self):
        for change, reason in ((dict(cutscene_flags=1), "cutscene_active"),
                               (dict(cutscene_flags=2), "cutscene_active"),
                               (dict(controls_disabled_mask=0x8000), "controls_disabled"),
                               (dict(control_eligible=False), "native_control_ineligible")):
            with self.subTest(change=change):
                self.calls.clear()
                result = self.run_once(snapshot(**change))
                self.assertEqual(result["reason"], reason)
                self.assertEqual(self.calls, [])
                result = self.run_once(snapshot(), snapshot(**change))
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["stage"], "after_query")
                self.assertNotIn("counts", result)

    def test_hour_boundary_discards_old_time_window_but_seconds_can_advance(self):
        for before, after in ((snapshot(clock_hour=7, clock_minute=59, clock_second=59),
                               snapshot(clock_hour=8, clock_minute=0, clock_second=0)),
                              (snapshot(clock_hour=23, clock_minute=59, clock_second=59),
                               snapshot(clock_hour=0, clock_minute=0, clock_second=0))):
            result = self.run_once(before, after)
            self.assertEqual(result["reason"], "time_window_changed_during_query")
            self.assertEqual(result["clock_after"], p.game_clock(after))
            self.assertNotIn("counts", result)
        result = self.run_once(snapshot(), snapshot(clock_second=16))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["clock_after"], [7, 30, 16])

    def test_missing_or_invalid_v2_clock_and_motion_fields_have_no_fallback(self):
        for change in (dict(protocol="GTATHAW1"), dict(clock_hour=None),
                       dict(clock_hour=24), dict(clock_minute=60), dict(clock_second=61), dict(clock_second=float("nan")),
                       dict(cutscene_flags=4), dict(ped_motion_flags=8), dict(controls_disabled_mask=-1),
                       dict(controls_disabled_mask=0x10000)):
            with self.subTest(change=change):
                result = self.run_once(snapshot(**change))
                self.assertFalse(result["accepted"])
        self.assertEqual(self.calls, [])
        airborne = self.run_once(snapshot(ped_motion_flags=2, grounded=False))
        self.assertTrue(airborne["accepted"])
        self.assertFalse(airborne["grounded"])

    def test_sixtieth_second_does_not_advance_timed_geometry_or_wrap_midnight(self):
        for hour in (7, 23):
            with self.subTest(hour=hour):
                before = snapshot(clock_hour=hour, clock_minute=59, clock_second=60)
                result = self.run_once(before)
                self.assertTrue(result["accepted"])
                self.assertEqual(result["clock_before"], [hour, 59, 60])
                self.assertEqual(result["query_hour"], float(hour))
                self.assertEqual(self.calls[-1][1]["hour"], float(hour))
                after = snapshot(clock_hour=(hour+1) % 24, clock_minute=0, clock_second=0)
                result = self.run_once(before, after)
                self.assertEqual(result["reason"], "time_window_changed_during_query")
                self.assertNotIn("counts", result)

    def test_live_ipl_activation_selects_only_confirmed_streams(self):
        activation = dict(active_streams=["barriers1"], streams=[dict(name="barriers1", slot=7,
                                                                   generation=2, loaded=True)])
        ipls = FakeObserver(activation, activation)
        result = p.probe_once(FakeObserver(snapshot(), snapshot()), Path("fixture.db"), 128,
                              query=self.lookup, ipls=ipls)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.calls[0][1]["active_streams"], ["barriers1"])
        self.assertEqual(result["conditional_ipls"], activation)
        self.assertFalse(self.calls[0][1]["include_dynamic"])

    def test_ipl_change_or_unreadable_pool_never_accepts_geometry(self):
        first = dict(active_streams=[], streams=[dict(slot=7, generation=2)])
        for after in (dict(active_streams=["crack"], streams=[]),
                      dict(active_streams=[], streams=[dict(slot=7, generation=3)]),
                      p.Rejected("ipl_load_transition")):
            result = p.probe_once(FakeObserver(snapshot(), snapshot()), Path("fixture.db"), 128,
                                  query=self.lookup, ipls=FakeObserver(first, after))
            self.assertFalse(result["accepted"])
            self.assertNotIn("counts", result)
            self.assertEqual(result["stage"], "after_ipls")
        self.calls.clear()
        result = p.probe_once(FakeObserver(snapshot()), Path("fixture.db"), 128,
                              query=self.lookup, ipls=FakeObserver(p.Rejected("missing_conditional_ipl")))
        self.assertEqual(result["reason"], "missing_conditional_ipl")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
