# Regression tests for automatic Z-offset calibration.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../klippy'))
import gcode
from extras import manual_probe, probe, prtouch


class FakeGCode(gcode.GCodeDispatch):
    def __init__(self):
        self.scripts = []
        self.messages = []

    def respond_info(self, message):
        self.messages.append(message)

    def respond_raw(self, message):
        self.messages.append(message)

    def run_script_from_command(self, script):
        self.scripts.append(script)


class FakeProbe:
    def __init__(self, z_offset):
        self.offsets = (-25., -16., z_offset)
        self.result = probe.calc_probe_z_average([
            manual_probe.create_probe_result((57., 46., z), self.offsets)
            for z in (1.9, 2.1)])

    def get_offsets(self):
        return self.offsets

    def start_probe_session(self, gcmd):
        return self

    def run_probe(self, gcmd):
        pass

    def pull_probed_results(self):
        return [self.result]

    def end_probe_session(self):
        pass


class PRTouchCalibrationTest(unittest.TestCase):
    def calibrate(self, z_offset=1.457, homing_z=0., apply=False):
        # Only the physical printer interfaces are replaced; run the real
        # command, probe-result helpers, and calibration finalizer.
        gcode_obj = FakeGCode()
        hardware_probe = FakeProbe(z_offset)
        original_result = tuple(hardware_probe.result)
        saved_config = {}
        configfile = types.SimpleNamespace(
            set=lambda section, option, value:
                saved_config.update({(section, option): value}))
        objects = {'gcode': gcode_obj, 'configfile': configfile}
        wrapper = prtouch.PRTouchZOffsetWrapper.__new__(
            prtouch.PRTouchZOffsetWrapper)
        wrapper.cfg = types.SimpleNamespace(
            sensor_x=32., sensor_y=30., random_offset=0.,
            bed_max_err=4., g29_xy_speed=150., g29_rdy_speed=2.5,
            probe_speed=2., check_bed_mesh_max_err=0.2,
            min_hold=3000, max_hold=50000, show_msg=True,
            probe_name='bltouch')
        wrapper.obj = types.SimpleNamespace(
            printer=types.SimpleNamespace(lookup_object=objects.__getitem__),
            gcode=gcode_obj, probe=hardware_probe,
            gcode_move=types.SimpleNamespace(
                get_status=lambda: {'homing_origin': (0., 0., homing_z, 0.)}),
            kin=types.SimpleNamespace(limits=[(0., 250.)] * 3),
            hx711s=types.SimpleNamespace(is_shutdown=False, is_timeout=False),
            dirzctl=types.SimpleNamespace(is_shutdown=False, is_timeout=False),
            toolhead=types.SimpleNamespace(wait_moves=lambda: None))
        params = {'APPLY_Z_ADJUST': '1'} if apply else {}
        gcmd = gcode_obj.create_gcode_command(
            'PRTOUCH_PROBE_ZOFFSET', 'PRTOUCH_PROBE_ZOFFSET', params)
        # Simulate the nozzle contacting the bed at frame Z=0.5mm.
        with mock.patch.object(wrapper, '_probe_times', return_value=0.5):
            wrapper.cmd_PRTOUCH_PROBE_ZOFFSET(gcmd)
        self.assertEqual(tuple(hardware_probe.result), original_result)
        return saved_config, gcode_obj

    def test_saved_offset_uses_raw_trigger_height(self):
        for old_offset in (0., 1.457, 2.6):
            with self.subTest(old_offset=old_offset):
                saved, gcode_obj = self.calibrate(z_offset=old_offset)
                self.assertEqual(saved[('bltouch', 'z_offset')], '1.500')
                self.assertIn('Probe at sensor: 2.000', gcode_obj.messages)
                self.assertFalse(any(script.startswith('SET_GCODE_OFFSET')
                                     for script in gcode_obj.scripts))

    def test_saved_offset_accounts_for_homing_origin(self):
        saved, _ = self.calibrate(homing_z=0.2)
        self.assertEqual(saved[('bltouch', 'z_offset')], '1.300')

    def test_live_adjustment_uses_existing_probe_offset(self):
        saved, gcode_obj = self.calibrate(apply=True)
        self.assertEqual(saved[('bltouch', 'z_offset')], '1.500')
        self.assertIn('SET_GCODE_OFFSET Z_ADJUST=-0.043000 MOVE=1',
                      gcode_obj.scripts)


if __name__ == '__main__':
    unittest.main()
