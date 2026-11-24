"""Offline temporal evidence prototype. It never certifies physical fold success.

Geometry can support a goal candidate. Frame/session freshness and observed
arm retreat are independent requirements; absent evidence stays unknown.
"""
from collections import deque
import math


class VisualEvidenceGate:
    def __init__(self,*,minimum_frames=5,stable_ns=350_000_000,max_age_ns=150_000_000,max_gap_ns=250_000_000):
        if minimum_frames<2 or min(stable_ns,max_age_ns,max_gap_ns)<=0:raise ValueError('Invalid temporal evidence window')
        self.minimum_frames=minimum_frames;self.stable_ns=stable_ns;self.max_age_ns=max_age_ns;self.max_gap_ns=max_gap_ns
        self.window=deque(maxlen=100);self.identity=None;self.last_id=-1;self.last_capture=None
    def begin(self,session,event):
        if type(session) is not int or type(event) is not int or not 0<session<0xffffffff or not 0<=event<0xffffffff:
            raise ValueError('Explicit valid session/event required')
        self.identity=(session,event);self.window.clear();self.last_id=-1;self.last_capture=None
    def observe(self,*,session,event,frame_id,captured_ns,now_ns,retreat_ready,visible,geometry_matches,center=None,area=None):
        def result(state,reason):return dict(state=state,reason=reason,stable_frames=len(self.window),task_success_certified=False)
        def unknown(reason):self.window.clear();return result('UNKNOWN',reason)
        if self.identity is None or (session,event)!=self.identity:return unknown('event_identity_missing_or_mismatched')
        if any(type(v) is not int for v in (frame_id,captured_ns,now_ns)) or frame_id<0:return unknown('invalid_frame_metadata')
        if captured_ns>now_ns or now_ns-captured_ns>self.max_age_ns:return unknown('stale_or_future_frame')
        if frame_id<=self.last_id or (self.last_capture is not None and captured_ns<=self.last_capture):return unknown('duplicate_or_reordered_frame')
        if self.last_capture is not None and captured_ns-self.last_capture>self.max_gap_ns:self.window.clear()
        self.last_id=frame_id;self.last_capture=captured_ns
        if retreat_ready is not True:return unknown('arm_retreat_unverified')
        if visible is not True:return unknown('cloth_missing_or_occluded')
        if not geometry_matches:self.window.clear();return result('MISMATCH','goal_geometry_differs')
        if center is None or len(center)!=2 or area is None or not all(math.isfinite(float(v)) for v in (*center,area)) or area<=0:
            return unknown('invalid_geometry')
        if self.window:
            _,first_center,first_area=self.window[0]
            if math.dist(center,first_center)>6 or abs(area/first_area-1)>.04:self.window.clear()
        self.window.append((captured_ns,tuple(center),float(area)))
        if len(self.window)>=self.minimum_frames and captured_ns-self.window[0][0]>=self.stable_ns:
            return result('MATCH_CANDIDATE','stable_geometry_only_hidden_layers_unobserved')
        return result('PENDING','waiting_for_stable_frames')
