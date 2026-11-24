"""Exchange a recent middle-camera frame between the task recorder and a viewer."""

import os
import time

import cv2


class PlacementCameraFeed:
    def __init__(self, logs):
        self.request = logs / '.placement_camera_request'
        self.frame = logs / '.placement_camera.jpg'
        self.temporary = logs / f'.placement_camera_{os.getpid()}.jpg'
        self.last_write = self.last_read = None
        self.rgb = None

    def publish(self, bgr):
        now = time.time()
        if self.last_write is not None and now - self.last_write < .18:
            return
        try:
            if not 0 <= now - self.request.stat().st_mtime < 1.0:
                return
            if cv2.imwrite(str(self.temporary), bgr, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                self.temporary.replace(self.frame)
                self.last_write = now
        except (OSError, cv2.error):
            pass  # Loss of the optional preview must never interrupt recording.

    def read_latest(self):
        now = time.time()
        if self.last_read is None or now - self.last_read >= .14:
            self.last_read = now
            self.rgb = None
            try:
                self.request.touch(mode=0o600)
                if 0 <= now - self.frame.stat().st_mtime < 1.0:
                    bgr = cv2.imread(str(self.frame))
                    if bgr is not None:
                        self.rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            except (OSError, cv2.error):
                pass  # A task can be waiting to start, or can end between reads.
        if self.rgb is None:
            raise TimeoutError('No recent middle-camera frame from the task')
        return self.rgb

    def close_writer(self):
        try:
            self.temporary.unlink(missing_ok=True)
            if self.last_write is not None:
                self.frame.unlink(missing_ok=True)
        except OSError:
            pass
