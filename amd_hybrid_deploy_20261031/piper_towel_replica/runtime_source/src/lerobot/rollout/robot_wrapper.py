# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Thread-safe robot wrapper for concurrent observation/action access."""

from __future__ import annotations

from threading import Lock
from typing import Any
import time

from lerobot.robots import Robot


class DispatchNotStarted(RuntimeError):
    command_sent = False
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class ThreadSafeRobot:
    """Lock-protected wrapper around a :class:`Robot` for use with background threads.

    When RTC inference runs in a background thread while the main loop
    executes actions, both threads may access the robot concurrently.
    This wrapper serialises ``get_observation`` and ``send_action`` calls.

    Read-only properties are proxied without the lock since they don't
    mutate hardware state.
    """

    def __init__(self, robot: Robot) -> None:
        self._robot = robot
        self._lock = Lock()

    # -- Lock-protected I/O --------------------------------------------------

    def get_observation(self) -> dict[str, Any]:
        with self._lock:
            return self._robot.get_observation()

    def send_action(self, action: dict[str, Any] | Any) -> Any:
        with self._lock:
            return self._robot.send_action(action)

    def get_feedback_snapshot(self, timeout: float):
        """Bound lock acquisition; return motor values and their paired SDK ages.

        No camera reads or actuator commands. The caller checks the whole
        freshness-to-dispatch deadline after this cache read returns too.
        """
        if timeout <= 0 or not self._lock.acquire(timeout=timeout):
            raise TimeoutError('Feedback lock did not become available within the authority window')
        try:
            observation=self._robot.get_motor_observation()
            timestamps=self._robot.last_feedback_timestamps
            return observation,timestamps,time.perf_counter_ns()
        finally:self._lock.release()

    def send_action_before(self, action, deadline_ns, stop_requested=lambda: False):
        """Apply the same deadline after acquiring the actual dispatch lock."""
        remaining=(deadline_ns-time.perf_counter_ns())/1e9
        if remaining<=0 or not self._lock.acquire(timeout=remaining):
            raise DispatchNotStarted('dispatch_deadline')
        try:
            if time.perf_counter_ns()>=deadline_ns:raise DispatchNotStarted('dispatch_deadline')
            if stop_requested():raise DispatchNotStarted('stop_requested')
            return self._robot.send_action(action)
        finally:self._lock.release()

    # -- Read-only proxies (no lock needed) -----------------------------------

    @property
    def observation_features(self) -> dict:
        return self._robot.observation_features

    @property
    def action_features(self) -> dict:
        return self._robot.action_features

    @property
    def name(self) -> str:
        return self._robot.name

    @property
    def robot_type(self) -> str:
        return self._robot.robot_type

    @property
    def cameras(self):
        return getattr(self._robot, "cameras", {})

    @property
    def is_connected(self) -> bool:
        return self._robot.is_connected

    @property
    def inner(self) -> Robot:
        """Access the underlying robot (e.g. for connect/disconnect)."""
        return self._robot
