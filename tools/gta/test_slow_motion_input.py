import struct
import unittest

from game_effects import SourcePacer
from input_mapping import map_actions
from slow_motion_input import SlowMotionInput, MAX_PENDING, MAX_AGE_SECONDS


def buttons(value):
    return struct.unpack_from('<I', value.packet, 0x24)[0]


class SlowMotionInputTests(unittest.TestCase):
    def test_quick_directional_tap_between_slow_frames_is_delivered_then_released(self):
        queue, pacer = SlowMotionInput(), SourcePacer()
        delivered=[]
        press=map_actions({'Grab','Backward','Left'},skating=True)
        forward=map_actions({'Forward'},skating=True)
        for poll in range(12):
            sample=press if poll==1 else forward
            queue.observe(sample,poll/60,scale=.3,context=(0,0))
            if pacer.due(poll/60,.3):delivered.append(queue.consume(poll/60))
        self.assertEqual([buttons(row) for row in delivered[:3]],[0,0x20,0])
        self.assertEqual(tuple(delivered[1].packet[:4]),(128,128,0,255))
        self.assertEqual(tuple(delivered[2].packet[:4]),tuple(forward.packet[:4]))

    def test_separate_taps_never_become_an_unobserved_chord(self):
        queue=SlowMotionInput()
        snapshots=[map_actions(actions,skating=True) for actions in
                   (set(),{'Grab'},set(),{'Flip'},set())]
        for i,sample in enumerate(snapshots):queue.observe(sample,i/100,scale=.3,context=0)
        actual=[queue.consume(.05+i/60) for i in range(4)]
        self.assertEqual([buttons(row) for row in actual],[0x20,0,0x80,0])
        self.assertNotIn(0xa0,[buttons(row) for row in actual])

    def test_held_buttons_do_not_queue_axes_and_normal_speed_is_latest_passthrough(self):
        for scale in (1.,.3):
            queue=SlowMotionInput()
            held=map_actions({'Ollie'},skating=True)
            queue.observe(held,0,scale=scale,context=0)
            self.assertIs(queue.consume(0),held)
            for i in range(1,10):
                latest=map_actions({'Ollie','Left' if i%2 else 'Right'},skating=True)
                queue.observe(latest,i/60,scale=scale,context=0)
            self.assertFalse(queue.queue)
            self.assertIs(queue.consume(.15),latest)

    def test_ramp_out_drains_existing_edges_without_losing_new_normal_speed_edge(self):
        queue=SlowMotionInput()
        snapshots=[map_actions(actions,skating=True) for actions in
                   (set(),{'Grind'},set(),{'Grab'})]
        for i,sample in enumerate(snapshots):
            queue.observe(sample,i/100,scale=.3 if i<2 else 1.,context=0)
        self.assertEqual([buttons(queue.consume(.04+i/60)) for i in range(3)],[0x10,0,0x20])
        self.assertIs(queue.consume(.1),snapshots[-1])

    def test_skitch_intent_with_equal_digital_mask_is_a_real_edge(self):
        queue=SlowMotionInput()
        # Backward suppresses skitch UP, but the owner still needs the chord edge.
        neutral=map_actions({'Backward'},skating=True)
        chord=map_actions({'Ollie','Grind','CameraRecenter','Backward'},skating=True)
        self.assertEqual(buttons(neutral),buttons(chord))
        for i,sample in enumerate((neutral,chord,neutral)):
            queue.observe(sample,i/100,scale=.3,context=0)
        self.assertIs(queue.consume(.03),chord)
        self.assertIs(queue.consume(.04),neutral)

    def test_pause_context_and_stale_backlog_never_replay_old_gestures(self):
        for change in ('clear','mode','age','backwards'):
            with self.subTest(change=change):
                queue=SlowMotionInput();tap=map_actions({'Flip'});neutral=map_actions(set())
                queue.observe(neutral,1,scale=.3,context=0)
                queue.observe(tap,1.01,scale=.3,context=0)
                queue.observe(neutral,1.02,scale=.3,context=0)
                now=1.03
                if change=='clear':queue.clear()
                elif change=='age':now=1.02+MAX_AGE_SECONDS+.01
                elif change=='backwards':now=.5
                queue.observe(neutral,now,scale=.3,context=1 if change=='mode' else 0)
                self.assertIs(queue.consume(now),neutral)
                self.assertFalse(queue.queue)

    def test_bounded_overload_keeps_latest_real_snapshot_without_combining(self):
        queue=SlowMotionInput()
        for i in range(MAX_PENDING+4):
            latest=map_actions({'Grab'} if i%2 else {'Flip'})
            queue.observe(latest,i/1000,scale=.3,context=0)
            self.assertLessEqual(len(queue.queue),MAX_PENDING)
        queued=[queue.consume(.02+i/1000) for i in range(len(queue.queue))]
        self.assertTrue(all(buttons(row) in (0x20,0x80) for row in queued))
        self.assertIs(queue.consume(.05),latest)

    def test_recenter_alone_does_not_create_source_gestures(self):
        queue=SlowMotionInput()
        queue.observe(map_actions(set()),0,scale=.3,context=1)
        latest=map_actions({'CameraRecenter'})
        queue.observe(latest,.01,scale=.3,context=1)
        self.assertFalse(queue.queue)
        self.assertIs(queue.consume(.02),latest)

    def test_bad_inputs_fail_before_changing_pending_snapshot(self):
        queue=SlowMotionInput();sample=map_actions({'Grab'})
        queue.observe(sample,0,scale=.3,context=0)
        for time,scale in ((float('nan'),.3),(1,0),(1,True),(True,.3)):
            with self.assertRaises(ValueError):queue.observe(sample,time,scale=scale,context=0)
        self.assertIs(queue.consume(.01),sample)


if __name__=='__main__':unittest.main()
