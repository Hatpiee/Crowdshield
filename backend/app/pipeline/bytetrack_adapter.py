"""ByteTrack tracker adapter (master spec §5/§10/§40 — global tracker;
BoT-SORT+ReID is reserved for chokepoint zones, which don't exist yet and
are explicitly out of scope here).

VERIFIED LIBRARY API (inspected directly on the installed `trackers==2.6.0`
/ `supervision==0.30.0`, not assumed from memory or older tutorials):

- `supervision.ByteTrack` still technically exists in 0.30.0 but is
  deprecated (`FutureWarning: ... deprecated since v0.28.0. It will be
  removed in v0.31.0` — one version later than this project's initial
  finding of "removal in 0.30.0"; the substance — deprecated, imminent
  removal, do not use — was correct). This adapter deliberately uses
  `trackers.ByteTrackTracker` instead, per the maintainers' own migration
  path.

- Real constructor signature (via `inspect.signature`):
  `ByteTrackTracker(lost_track_buffer=30, frame_rate=30.0,
  track_activation_threshold=0.7, minimum_consecutive_frames=2,
  minimum_iou_threshold=0.1, high_conf_det_threshold=0.6,
  state_estimator_class=XYXYStateEstimator, iou=None)`. This project
  exposes three of these as CONFIGURABLE env vars, all genuinely existing
  on the library, matched to their real names: TRACKER_LOST_TRACK_BUFFER,
  TRACKER_MINIMUM_CONSECUTIVE_FRAMES (the two named by this phase's
  decisions), and TRACKER_TRACK_ACTIVATION_THRESHOLD (added after a real
  finding — see below). All other params are left at the library's own
  defaults. `frame_rate` is NOT a fixed config value — it's passed in
  per-instance from the actual source video's real fps (see `__init__`),
  since different videos have different frame rates and hardcoding 30.0
  would silently mis-scale the lost-track buffer for any video that isn't
  ~30fps.

- REAL-FOOTAGE FINDING re: `track_activation_threshold` (library default
  0.7 — the minimum detection confidence required to CREATE a brand-new
  track): this is a distinct concept from `DETECTOR_CONFIDENCE_THRESHOLD`
  (Phase 6, default 0.25 — the minimum confidence to count as a detection
  at all). Verified on real footage (people_clip.mp4): most detections in
  a dense/aerial scene land well under 0.7 confidence (e.g. frame 90's 19
  detections topped out at 0.657), so at the library's own default,
  effectively only one high-confidence person in the whole scene ever gets
  a confirmed track_id — everyone else's detections keep arriving (and
  can still extend an already-confirmed track via ByteTrack's low-
  confidence second-stage association) but never clear the bar to START a
  new track. This is a genuine tuning gap between the two libraries'
  confidence philosophies, not a bug in this adapter. Exposed as
  TRACKER_TRACK_ACTIVATION_THRESHOLD, default left at the library's own
  0.7 so this project doesn't silently change tracking behavior — lower it
  (e.g. towards 0.25-0.4) via .env for multi-person tracking on similar
  footage.

- Real `update()` signature: `update(detections: sv.Detections, frame:
  ndarray | None = None, timestamp: float | None = None) -> sv.Detections`.
  Matches this project's expectation. Its own docstring states "Detection
  order may differ from input" and "Unmatched detections have tracker_id
  of -1" — both confirmed empirically below and handled explicitly.

- LOST-TRACK INVESTIGATION (decision #3), verified empirically by feeding
  a real ByteTrackTracker a moving detection for several frames, then one
  frame with zero detections, and inspecting what came back:
  `update()`'s RETURN VALUE only ever contains entries for the detections
  passed into that specific call — on the empty frame it returned an empty
  Detections object. So lost tracks are NOT exposed through update()'s
  return value.
  However, the tracker object itself exposes `self.tracks` (a list of
  internal `ByteTrackTracklet` objects) which DOES retain tracks still
  within the lost-track buffer after they stop being matched, each with a
  `.tracker_id` and `.time_since_update` (frames since last real match).
  BUT: that tracklet's own position estimate is a Kalman-filter
  extrapolation (empirically confirmed to advance beyond the last real
  detected box, consistent with continuing the previous velocity) — using
  it directly would violate decision #4's "never fabricate a new position
  for a lost track" rule. So: lost tracks ARE exposed by the library (via
  `self.tracks`, not `update()`'s return), and this adapter surfaces them
  with `is_lost=True` — but deliberately ignores the library's own
  extrapolated box and instead carries forward this adapter's own
  internally-remembered last REAL match (point/local_scale/confidence),
  per decision #3's explicit anti-fabrication requirement.
"""

import numpy as np
import supervision as sv
from trackers import ByteTrackTracker as _LibByteTrackTracker

from app.core.config import settings
from app.pipeline.detection import DetectionResult
from app.pipeline.track import Track, TrackingResult, TrajectoryPoint
from app.pipeline.tracker import Tracker


def _compute_velocity(
    trajectory: list[TrajectoryPoint],
) -> tuple[tuple[float, float] | None, float | None]:
    """Velocity from the two most recent trajectory points, using the REAL
    elapsed wall-clock time between them (never assumed uniform per-frame
    timing). None/None if fewer than two points exist — never fabricated
    as zero (decision #4)."""
    if len(trajectory) < 2:
        return None, None

    (_, t0, p0), (_, t1, p1) = trajectory[-2], trajectory[-1]
    dt = t1 - t0
    if dt <= 0:
        # Duplicate/out-of-order timestamps — no real elapsed time to
        # divide by, so no honest velocity can be computed.
        return None, None

    vx = (p1.x - p0.x) / dt
    vy = (p1.y - p0.y) / dt
    speed = (vx**2 + vy**2) ** 0.5
    return (vx, vy), speed


class ByteTrackAdapter(Tracker):
    """Adapts trackers.ByteTrackTracker to this project's Tracker
    interface. See module docstring for verified library API details.

    STATEFUL — construct one fresh instance per video/session, never
    reuse across two videos (see Tracker's docstring for why this is a
    hard, constitutional requirement, not just a correctness suggestion).
    """

    def __init__(self, fps: float):
        if fps <= 0:
            raise ValueError(f"fps must be > 0, got {fps}")

        self._lib_tracker = _LibByteTrackTracker(
            lost_track_buffer=settings.TRACKER_LOST_TRACK_BUFFER,
            frame_rate=fps,
            minimum_consecutive_frames=settings.TRACKER_MINIMUM_CONSECUTIVE_FRAMES,
            track_activation_threshold=settings.TRACKER_TRACK_ACTIVATION_THRESHOLD,
        )

        # This adapter's own memory of each track_id's real (never
        # extrapolated) history — the source of truth for lost-track
        # carry-forward, per decision #3.
        self._trajectories: dict[int, list[TrajectoryPoint]] = {}
        self._last_attrs: dict[int, tuple[float, float]] = {}  # (local_scale, confidence)

    def update(self, detections: DetectionResult) -> TrackingResult:
        for detection in detections.detections:
            if detection.raw_box is None:
                raise ValueError(
                    "ByteTrackAdapter requires every Detection.raw_box to be "
                    "populated; got None. This should never happen from "
                    "YOLO11nDetector's own output — check whatever produced "
                    "this DetectionResult."
                )

        if detections.detections:
            boxes = np.array(
                [d.raw_box for d in detections.detections], dtype=np.float32
            )
            confidences = np.array(
                [d.confidence for d in detections.detections], dtype=np.float32
            )
            class_ids = np.zeros(len(detections.detections), dtype=int)
            sv_detections = sv.Detections(
                xyxy=boxes, confidence=confidences, class_id=class_ids
            )
            # Boxes are unchanged by the tracker (verified empirically —
            # it only associates IDs), so this lookup is a reliable way to
            # map tracked results back to their source Detection without
            # assuming list order is preserved (decision #2).
            box_to_detection = {
                tuple(box.tolist()): d
                for box, d in zip(boxes, detections.detections)
            }
        else:
            sv_detections = sv.Detections.empty()
            box_to_detection = {}

        tracked = self._lib_tracker.update(
            sv_detections, timestamp=detections.timestamp_seconds
        )

        matched_track_ids: set[int] = set()
        tracks_out: list[Track] = []

        for i in range(len(tracked)):
            track_id = int(tracked.tracker_id[i])
            if track_id == -1:
                # Not yet confirmed (fewer than
                # TRACKER_MINIMUM_CONSECUTIVE_FRAMES consecutive matches) —
                # the library hasn't assigned a stable identity yet, so
                # this isn't a real track_id and isn't surfaced.
                continue

            box_key = tuple(tracked.xyxy[i].tolist())
            source_detection = box_to_detection.get(box_key)
            if source_detection is None:
                raise RuntimeError(
                    f"Could not correlate tracked box {box_key} back to its "
                    "source Detection by coordinates — the underlying "
                    "tracker may have altered box coordinates unexpectedly."
                )

            trajectory = self._trajectories.setdefault(track_id, [])
            trajectory.append(
                (detections.frame_number, detections.timestamp_seconds, source_detection.point)
            )
            if len(trajectory) > settings.TRACK_HISTORY_LENGTH:
                del trajectory[0]

            self._last_attrs[track_id] = (
                source_detection.local_scale,
                source_detection.confidence,
            )
            velocity, speed = _compute_velocity(trajectory)

            tracks_out.append(
                Track(
                    track_id=track_id,
                    point=source_detection.point,
                    local_scale=source_detection.local_scale,
                    confidence=source_detection.confidence,
                    trajectory=list(trajectory),
                    velocity=velocity,
                    speed=speed,
                    is_lost=False,
                    frames_since_last_matched=0,
                )
            )
            matched_track_ids.add(track_id)

        # Lost-but-not-yet-expired tracks (decision #3): present in the
        # library's internal buffer, not matched this frame. Carry forward
        # OUR OWN last real match — never the library's Kalman-extrapolated
        # position (see module docstring investigation notes).
        #
        # FRAGILITY WARNING: `self._lib_tracker.tracks` is a PRIVATE/
        # INTERNAL attribute of trackers.ByteTrackTracker — it is not part
        # of the library's documented public API (the public surface is
        # `update()`, which does not expose lost tracks at all; see the
        # module docstring's investigation notes). It is not covered by
        # the library's own API stability guarantees, and nothing prevents
        # a future `trackers` release from renaming, restructuring, or
        # removing it. ANY future upgrade of the `trackers` package version
        # MUST re-verify this specific attribute (name, shape, and that
        # each tracklet still exposes `.tracker_id` and `.time_since_update`
        # the same way) still works before the upgrade is considered safe —
        # do not assume it carries forward silently.
        for tracklet in self._lib_tracker.tracks:
            track_id = int(tracklet.tracker_id)
            if track_id == -1 or track_id in matched_track_ids:
                continue
            if track_id not in self._trajectories:
                # Known to the library but never had a confirmed real match
                # recorded by this adapter — nothing honest to carry
                # forward.
                continue

            trajectory = self._trajectories[track_id]
            local_scale, confidence = self._last_attrs[track_id]
            _, _, last_point = trajectory[-1]
            velocity, speed = _compute_velocity(trajectory)

            tracks_out.append(
                Track(
                    track_id=track_id,
                    point=last_point,
                    local_scale=local_scale,
                    confidence=confidence,
                    trajectory=list(trajectory),
                    velocity=velocity,
                    speed=speed,
                    is_lost=True,
                    frames_since_last_matched=int(tracklet.time_since_update),
                )
            )

        # Drop our own memory of any track the library has fully expired
        # (pruned past lost_track_buffer) — otherwise a long video with
        # many short-lived tracks would grow these dicts unboundedly.
        still_known = {
            int(t.tracker_id) for t in self._lib_tracker.tracks if int(t.tracker_id) != -1
        }
        for stale_id in set(self._trajectories) - still_known:
            del self._trajectories[stale_id]
            del self._last_attrs[stale_id]

        return TrackingResult(
            frame_number=detections.frame_number,
            timestamp_seconds=detections.timestamp_seconds,
            tracks=tracks_out,
            tracker_name="bytetrack",
            source_detection_count=len(detections.detections),
        )
