"""Explicit POSITION streaming through official APIs; never opens hardware."""

from copy import deepcopy
import math
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

from bimanual_teleop.control.arm.cartesian import TianjiCartesianExecutor
from bimanual_teleop.devices.tianji.driver import TianjiDriver
from bimanual_teleop.devices.tianji.sdk import ControlSDK
from bimanual_teleop.types import RobotTarget
from tests.support.tianji import FakeSDK, Sink, TianjiFixture, packet
from tests.test_tianji_cartesian import FakeDriver, FakeKinematics


class PositionSDK(FakeSDK):
    """An official-boundary fake with independently controllable arm replies."""

    def __init__(self, driver):
        super().__init__(driver)
        self.position_sides = (0, 1)
        self.position_state = 1
        self.position_error = 0
        self.echo_position = True

    def engage_position(self, mask, targets, expiry):
        self.calls.append(("engage_position", (mask, targets, expiry)))
        submitted = time.monotonic_ns()
        if self.echo_position:
            value = deepcopy(self.driver._packet)
            for index in self.position_sides:
                if mask & (1 << index):
                    value.sequence[index] += 1
                    value.state[index] = self.position_state
                    value.error[index] = self.position_error
                    value.target[index*7:index*7+7] = targets[index*7:index*7+7]
            value.packet_index += 1
            value.received_ns = time.monotonic_ns()
            self.driver._on_feedback(value)
        return submitted


class PositionNativeTests(unittest.TestCase):
    def setUp(self):
        self.sdk = ControlSDK.__new__(ControlSDK)
        self.sdk.robot = Mock()
        self.sdk._opened = True
        self.sdk._lock = threading.RLock()
        self.sdk.cancelled = lambda: False

    def engage(self):
        return self.sdk.engage_position(3, list(range(14)), time.monotonic_ns()+50_000_000)

    def test_both_seeds_and_modes_are_one_atomic_official_submission(self):
        self.engage()
        self.assertEqual(self.sdk.robot.method_calls, [
            call.clear_set(), call.set_joint_cmd_pose("A", list(range(7))), call.set_state("A", 1),
            call.set_joint_cmd_pose("B", list(range(7, 14))), call.set_state("B", 1), call.send_cmd()])
        self.sdk.robot.set_impedance_type.assert_not_called()

    def test_second_arm_rejection_discards_unsubmitted_group(self):
        self.sdk.robot.set_state.side_effect = [True, False]
        with self.assertRaisesRegex(RuntimeError, "position mode"):
            self.engage()
        self.sdk.robot.send_cmd.assert_not_called()
        self.assertEqual(self.sdk.robot.clear_set.call_count, 2)

    def test_expired_seed_never_constructs_or_enables(self):
        with self.assertRaisesRegex(RuntimeError, "expired"):
            self.sdk.engage_position(3, [0.]*14, time.monotonic_ns()-1)
        self.assertEqual(self.sdk.robot.method_calls, [])

    def test_cancelled_build_cannot_send_partial_position_group(self):
        cancelled = threading.Event()
        self.sdk.cancelled = cancelled.is_set
        self.sdk.robot.set_state.side_effect = lambda *_: cancelled.set() or True
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.engage()
        self.sdk.robot.send_cmd.assert_not_called()
        self.assertEqual(self.sdk.robot.clear_set.call_count, 2)


class PositionStreamDriverTests(unittest.TestCase):
    def setUp(self):
        self.driver = TianjiDriver("192.0.2.1", watchdog_s=.08, engagement_timeout_s=1.)
        self.native, self.sink = PositionSDK(self.driver), Sink()
        self.driver._sdk, self.driver._sink = self.native, self.sink
        self.driver._on_feedback(packet())
        self.driver.configure(TianjiFixture.make_profile(("left", "right")))
        self.native.calls.clear()
        self.addCleanup(self.driver.close)

    def command(self):
        fixture = TianjiFixture.__new__(TianjiFixture)
        fixture.driver = self.driver
        return fixture.command()

    def test_position_uses_actual_seed_and_confirms_both_arms(self):
        seed = self.driver.get_latest()
        self.driver.engage(control_mode="position")
        self.assertTrue(self.driver.engaged)
        self.assertTrue(self.driver._mode_confirmed)
        self.assertEqual(self.driver._control_mode, "position")
        self.assertIs(self.driver.engagement_sample, seed)
        name, (mask, targets, _) = self.native.calls[0]
        self.assertEqual((name, mask), ("engage_position", 3))
        self.assertEqual(targets, [math.degrees(q) for side in ("left", "right")
                                  for q in seed.payload.arms[side].joints.position_rad])
        for side in ("left", "right"):
            self.assertEqual(self.driver.get_latest().payload.arms[side].state, 1)
        event = [e for e in self.sink.events if getattr(e, "kind", None) == "tianji.engagement_mode_reported"][-1]
        self.assertEqual(event.details["control_mode"], "position")

    def test_first_target_keeps_startup_grace_then_uses_runtime_watchdog(self):
        self.driver.engage(control_mode="position")
        self.assertGreater(self.driver._deadline_ns-time.monotonic_ns(), 800_000_000)
        self.assertTrue(self.driver.submit(self.command()).accepted)
        self.assertEqual(self.native.calls[-1][0], "submit")
        self.assertLessEqual(self.driver._deadline_ns-time.monotonic_ns(), self.driver.watchdog_ns)
        self.assertGreater(self.driver._deadline_ns-time.monotonic_ns(), 0)
        self.assertTrue(self.driver.engaged)

    def test_unchanged_old_position_feedback_does_not_confirm_new_engagement(self):
        value = deepcopy(self.driver._packet)
        value.state[:] = [1, 1]
        value.sequence[:] = [n+1 for n in value.sequence]
        value.received_ns = time.monotonic_ns()
        self.driver._on_feedback(value)
        self.native.echo_position = False
        self.driver.engagement_timeout_ns = 20_000_000
        with self.assertRaisesRegex(RuntimeError, "did not confirm position"):
            self.driver.engage(control_mode="position")
        self.assertFalse(self.driver.engaged)
        self.assertIn(("hold", (3,)), self.native.calls)

    def test_one_fresh_arm_cannot_confirm_a_dual_arm_engagement(self):
        self.native.position_sides = (0,)
        self.driver.engagement_timeout_ns = 20_000_000
        with self.assertRaisesRegex(RuntimeError, "did not confirm position"):
            self.driver.engage(control_mode="position")
        self.assertFalse(self.driver.engaged)

    def test_cartesian_feedback_cannot_confirm_position(self):
        self.native.position_state = 3
        self.driver.engagement_timeout_ns = 20_000_000
        with self.assertRaisesRegex(RuntimeError, "did not confirm position"):
            self.driver.engage(control_mode="position")
        self.assertFalse(self.driver.engaged)

    def test_controller_entry_error_stops_and_never_submits_a_following_target(self):
        self.native.position_state, self.native.position_error = 100, 4
        with self.assertRaisesRegex(RuntimeError, "Cannot confirm position.*controller error 4"):
            self.driver.engage(control_mode="position")
        self.assertFalse(self.driver.engaged)
        self.assertFalse(self.driver.submit(self.command()).accepted)
        self.assertNotIn("submit", [name for name, _ in self.native.calls])
        self.assertIn(("hold", (3,)), self.native.calls)

    def test_expired_stream_watchdog_stops_with_position_rsta(self):
        self.driver.engage(control_mode="position")
        self.driver._deadline_ns = time.monotonic_ns()-1
        with patch.object(self.driver._stop, "wait", side_effect=(False, True)):
            self.driver._watch()
        self.assertFalse(self.driver.engaged)
        self.assertIn("Accepted target expired", self.driver.motion_stop["reason"])
        self.assertEqual(self.native.calls[-1], ("hold", (3,)))
        self.assertFalse(self.driver.submit(self.command()).accepted)

    def test_unexpected_mode_change_during_streaming_stops(self):
        self.driver.engage(control_mode="position")
        value = deepcopy(self.driver._packet)
        value.state[1], value.impedance_type[1] = 3, 2
        value.sequence[:] = [n+1 for n in value.sequence]
        value.received_ns = time.monotonic_ns()
        self.driver._on_feedback(value)
        result = self.driver.submit(self.command())
        self.assertFalse(result.accepted)
        self.assertIn("right is not reporting the engaged position mode", result.reason)
        self.assertEqual(self.native.calls[-1], ("hold", (3,)))

    def test_close_disables_only_the_selected_position_arm(self):
        self.driver.configure(TianjiFixture.make_profile(("left",)))
        self.driver.engage(control_mode="position")
        self.driver.close()
        self.assertEqual([args[0] for name, args in self.native.calls if name == "disable"], [1])
        self.assertEqual(self.driver.get_latest().payload.arms["left"].state, 0)

    def test_invalid_mode_rejected_without_state_change_or_sdk_submission(self):
        with self.assertRaisesRegex(ValueError, "control_mode"):
            self.driver.engage(control_mode="pvt")
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.driver._control_mode, "cartesian")
        self.assertFalse(self.driver.engaged)

    def test_default_engagement_remains_cartesian(self):
        self.driver.engage()
        self.assertEqual(self.native.calls[0][0], "engage")
        self.assertEqual(self.driver._control_mode, "cartesian")
        self.assertEqual([arm.state for arm in self.driver.get_latest().payload.arms.values()], [3, 3])


class PositionExecutorTests(unittest.TestCase):
    def test_position_is_explicitly_forwarded_and_keeps_constrained_group_submission(self):
        class Driver(FakeDriver):
            def engage(self, *, control_mode="cartesian"):
                self.mode = control_mode
                super().engage()
        driver, kine = Driver(), FakeKinematics()
        executor = TianjiCartesianExecutor(driver, kine)
        executor.engage(control_mode="position")
        self.assertEqual(driver.mode, "position")
        now = time.monotonic_ns()
        poses = {side: kine.fk(side, (.001, 0., 0., 0., 0., 0., 0.)) for side in ("left", "right")}
        result = executor.submit(RobotTarget("position", poses, (), now, now+50_000_000, "test"))
        self.assertTrue(result.accepted, result.reason)
        self.assertEqual(len(driver.commands), 1)
        self.assertEqual(set(driver.commands[0].payload.targets), {"left", "right"})
        for target in driver.commands[0].payload.targets.values():
            self.assertGreater(target[0], 0)
            self.assertLess(target[0], .001)

    def test_default_keeps_legacy_driver_engage_signature(self):
        driver = FakeDriver()  # This fixture deliberately has engage(self).
        executor = TianjiCartesianExecutor(driver, FakeKinematics())
        executor.engage()
        self.assertTrue(driver.engaged)

    def test_invalid_mode_cannot_engage_driver(self):
        driver = FakeDriver()
        executor = TianjiCartesianExecutor(driver, FakeKinematics())
        with self.assertRaisesRegex(ValueError, "control_mode"):
            executor.engage(control_mode="pvt")
        self.assertFalse(driver.engaged)


if __name__ == "__main__":
    unittest.main()
