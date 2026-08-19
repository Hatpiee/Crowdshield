"""Reasoner Stability / Acute-Hazard synthetic E2E validation phase: a
CLEARLY SYNTHETIC (never claimed as real footage) BEFORE -> EVENT ->
AFTERMATH video fixture, generated purely from OpenCV drawing primitives —
same "no external test-video download, no ffmpeg dependency" convention as
tests/fixtures/synthetic_video.py, extended to a size/duration realistic
enough to exercise the REAL YOLO/DIS-optical-flow/AcuteHazardDetector/VLM
pipeline (synthetic_video.py's own 64x64/10-frame fixture is deliberately
too small/short for that — this is a separate, larger fixture for a
separate purpose).

Per the developer's explicit instruction: this MUST NOT pretend to be real
blast footage, and must not hard-code/bypass any downstream logic — every
frame is genuinely fed through the real deterministic pipeline (real
optical flow, real detection, real AcuteHazardDetector quorum/EMA logic),
so whether ACUTE_HAZARD actually fires is a genuine, unforced outcome of
the synthetic pixel content, not a scripted result.

============================================================
REAL ROOT CAUSE FOUND AND FIXED (Acute-Hazard Precision phase) — NOT the
burst geometry, NOT the ambient-dot motion (two earlier, wrong hypotheses)
============================================================
Two earlier iterations of this fixture (a wobbling-dot version, then a
stationary-dot version) both independently produced the EXACT SAME real
`reverse_flow_cell_fraction=0.0052` reading on every real E2E run,
regardless of what the ambient dots or burst shape did — proving neither
was the actual cause. The real cause: `_noisy_background()` used to
REGENERATE fresh independent Gaussian noise EVERY frame, including during
the "calm" BEFORE/AFTERMATH stretches. That per-frame decorrelated noise
gives DIS optical flow spurious, incoherent per-cell motion readings even
though nothing is "really" moving — and `REVERSE_FLOW_MIN_BASELINE_
OBSERVATIONS=15` is comfortably within the 40 BEFORE frames, so a handful
of cells accumulate an ESTABLISHED reverse-flow baseline direction from
pure noise. The burst's own real motion then genuinely deviates from that
noise-derived (not meaningful) baseline in a cell or two, and with 20
AFTERMATH frames (also noise-regenerated in the old version) the required
`REVERSE_FLOW_PERSISTENCE_MIN_COUNT=6`-of-`10` window is easily reached.

Fixed by generating the noise texture ONCE (module-level, fixed seed) and
REUSING the identical frame for every BEFORE/AFTERMATH-stage background —
optical flow correctly reads zero motion against an unchanging image, so
no reverse-flow baseline ever spuriously establishes during calm periods.

Design (BEFORE / EVENT / AFTERMATH):
  - BEFORE: `baseline_frames` frames of a FIXED (identical every frame)
    textured background with a handful of small, STATIONARY dots — real,
    non-degenerate texture for DIS optical flow to track, with genuinely
    zero real motion (not noise-driven pseudo-motion).
  - EVENT: `event_frames` frames of a rapidly, radially expanding bright
    orange/white burst from the frame center (motion-blur streaks, a soft
    Gaussian-blurred haze, outward-scattering debris trails) — a
    genuinely spatially localized, high-magnitude, radially-divergent
    motion pattern (`flow_divergence`'s own target signature). The
    ambient dots ALSO begin moving radially OUTWARD from the same burst
    center at this point — per the developer's explicit Step 11
    instruction, a coherent "crowd reacts by moving away from the event"
    visual/motion cue in the SAME direction as the burst's own debris,
    never a direction that could read as a contradictory reversal.
  - AFTERMATH: `aftermath_frames` frames of a persistent gray "smoke haze"
    overlay atop the SAME fixed background, slowly fading back toward
    baseline, with the dots continuing their outward drift before
    settling — a visibly CHANGED, not identical, post-event state.
"""

from pathlib import Path

import cv2
import numpy as np

WIDTH = 640
HEIGHT = 480
FPS = 30.0

DEFAULT_BASELINE_FRAMES = 40
DEFAULT_EVENT_FRAMES = 4
DEFAULT_AFTERMATH_FRAMES = 20

_RNG_SEED = 20260819  # fixed for reproducibility across runs/CI, not real-world timing

_CENTER = (WIDTH // 2, HEIGHT // 2)
_NUM_DOTS = 6
_DOT_START_POSITIONS = [
    (60 + (i * 90) % (WIDTH - 120), 80 + (i * 53) % (HEIGHT - 160)) for i in range(_NUM_DOTS)
]


def _fixed_background() -> np.ndarray:
    """Generated ONCE (module-level, fixed seed) and reused identically
    across every calm frame — see module docstring's real-root-cause note.
    A single static realization still gives DIS optical flow real texture
    to lock onto; it just never changes frame-to-frame on its own, so
    optical flow correctly reads zero motion against it."""
    rng = np.random.default_rng(_RNG_SEED)
    base = np.full((HEIGHT, WIDTH, 3), 90, dtype=np.int16)
    noise = rng.normal(0.0, 6.0, size=(HEIGHT, WIDTH, 3))
    return np.clip(base + noise, 0, 255).astype(np.uint8)


_FIXED_BACKGROUND = _fixed_background()


def _dot_position(i: int, frames_since_event_start: int | None) -> tuple[int, int]:
    start_x, start_y = _DOT_START_POSITIONS[i]
    if frames_since_event_start is None or frames_since_event_start <= 0:
        return start_x, start_y

    # Coherent outward drift from the SAME center the burst expands from,
    # starting at the event and continuing through the aftermath —
    # matches the burst's own radial direction, never contradicts it.
    dx, dy = start_x - _CENTER[0], start_y - _CENTER[1]
    norm = max(1.0, (dx * dx + dy * dy) ** 0.5)
    speed_px_per_frame = 3.0
    travel = min(frames_since_event_start, 15) * speed_px_per_frame
    new_x = int(start_x + (dx / norm) * travel)
    new_y = int(start_y + (dy / norm) * travel)
    return max(10, min(WIDTH - 10, new_x)), max(10, min(HEIGHT - 10, new_y))


def _draw_ambient_dots(frame: np.ndarray, frames_since_event_start: int | None) -> np.ndarray:
    frame = frame.copy()
    for i in range(_NUM_DOTS):
        cx, cy = _dot_position(i, frames_since_event_start)
        cv2.circle(frame, (cx, cy), 6, (60, 60, 60), -1, cv2.LINE_AA)
    return frame


def _draw_event_burst(frame: np.ndarray, stage_fraction: float) -> np.ndarray:
    """stage_fraction in (0, 1]: 0 = just starting, 1 = peak expansion."""
    frame = frame.copy()
    center = _CENTER
    max_radius = int(min(WIDTH, HEIGHT) * 0.42)
    radius = max(8, int(max_radius * stage_fraction))

    overlay = frame.copy()
    # Radiating streaks FIRST (under the fireball) — a shockwave/motion-
    # blur cue radiating out past the fireball's own edge.
    num_streaks = 32
    for i in range(num_streaks):
        angle = (2 * np.pi / num_streaks) * i
        inner_r = radius * 0.3
        outer_r = radius * 1.6
        x1 = int(center[0] + inner_r * np.cos(angle))
        y1 = int(center[1] + inner_r * np.sin(angle))
        x2 = int(center[0] + outer_r * np.cos(angle))
        y2 = int(center[1] + outer_r * np.sin(angle))
        cv2.line(overlay, (x1, y1), (x2, y2), (60, 120, 220), 3, cv2.LINE_AA)

    cv2.circle(overlay, center, radius, (30, 140, 255), -1, cv2.LINE_AA)  # bright orange (BGR)
    cv2.circle(overlay, center, max(4, radius // 2), (245, 245, 245), -1, cv2.LINE_AA)  # white-hot core
    overlay = cv2.GaussianBlur(overlay, (25, 25), 0)  # soft smoke/blast-haze edges, not a crisp shape
    frame = cv2.addWeighted(overlay, 0.85, frame, 0.15, 0)

    # Debris scattering radially outward, drawn as short trailing LINE
    # segments (motion-streaked), not static dots.
    num_debris = 24
    for i in range(num_debris):
        angle = (2 * np.pi / num_debris) * i
        debris_r = radius * (0.9 + 0.5 * ((i % 3) / 2.0))
        dx = int(center[0] + debris_r * np.cos(angle))
        dy = int(center[1] + debris_r * np.sin(angle))
        trail_dx = int(center[0] + (debris_r - 18) * np.cos(angle))
        trail_dy = int(center[1] + (debris_r - 18) * np.sin(angle))
        if 0 <= dx < WIDTH and 0 <= dy < HEIGHT:
            cv2.line(frame, (trail_dx, trail_dy), (dx, dy), (15, 15, 15), 2, cv2.LINE_AA)
    return frame


def _draw_aftermath_haze(frame: np.ndarray, fade_fraction: float) -> np.ndarray:
    """fade_fraction 1.0 = right after the event (heaviest haze), 0.0 = back to baseline."""
    haze = np.full_like(frame, 140)
    alpha = 0.5 * fade_fraction
    return cv2.addWeighted(haze, alpha, frame, 1.0 - alpha, 0)


def generate_frames(
    baseline_frames: int = DEFAULT_BASELINE_FRAMES,
    event_frames: int = DEFAULT_EVENT_FRAMES,
    aftermath_frames: int = DEFAULT_AFTERMATH_FRAMES,
):
    """Yields (stage_name, frame_bgr_uint8) tuples in sequence."""
    for _t in range(baseline_frames):
        frame = _draw_ambient_dots(_FIXED_BACKGROUND, frames_since_event_start=None)
        yield "BEFORE", frame

    for e in range(event_frames):
        stage_fraction = (e + 1) / event_frames
        frame = _draw_event_burst(_FIXED_BACKGROUND, stage_fraction)
        frame = _draw_ambient_dots(frame, frames_since_event_start=e + 1)
        yield "EVENT", frame

    for a in range(aftermath_frames):
        fade_fraction = max(0.0, 1.0 - (a / max(1, aftermath_frames - 1)))
        frame = _draw_aftermath_haze(_FIXED_BACKGROUND, fade_fraction)
        frame = _draw_ambient_dots(frame, frames_since_event_start=event_frames + a + 1)
        yield "AFTERMATH", frame


def generate_synthetic_acute_event_mp4(
    path: Path,
    baseline_frames: int = DEFAULT_BASELINE_FRAMES,
    event_frames: int = DEFAULT_EVENT_FRAMES,
    aftermath_frames: int = DEFAULT_AFTERMATH_FRAMES,
    fps: float = FPS,
) -> int:
    """Writes the synthetic video to `path` via cv2.VideoWriter (mp4v, same
    dependency-free convention as synthetic_video.py). Returns the total
    frame count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (WIDTH, HEIGHT))
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open VideoWriter for {path}")
    count = 0
    try:
        for _stage, frame in generate_frames(baseline_frames, event_frames, aftermath_frames):
            writer.write(frame)
            count += 1
    finally:
        writer.release()
    return count
