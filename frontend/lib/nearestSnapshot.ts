// Phase 22, Step 4 / Resolution 2: shared client-side derivation for "the
// value as of right now" — consumed by every widget that needs it (current
// risk badge, density/pressure/congestion/flow stat cards, heatmap viewer)
// instead of each one re-deriving the same lookup independently. No new
// backend endpoint: every consumer already has the FULL relevant array
// fetched (crowd_metrics_snapshots timeseries, or the session's full
// heatmap list) and derives from it here.

export interface TimestampedItem {
  timestamp_seconds: number;
}

// Simple linear scan, not a binary search over a sorted array or similar:
// deliberately not warranted here — the realistic array sizes involved
// (crowd_metrics_snapshots at ~1 row/second, heatmap snapshots at ~1 row
// per HEATMAP_GENERATION_INTERVAL_SECONDS=5s) top out at a few thousand
// items even for a long session, well within what a per-tick linear scan
// handles instantly.
export function findNearestByTimestamp<T extends TimestampedItem>(
  items: T[],
  targetSeconds: number
): T | null {
  if (items.length === 0) return null;
  let nearest = items[0];
  let bestDelta = Math.abs(nearest.timestamp_seconds - targetSeconds);
  for (const item of items) {
    const delta = Math.abs(item.timestamp_seconds - targetSeconds);
    if (delta < bestDelta) {
      nearest = item;
      bestDelta = delta;
    }
  }
  return nearest;
}

// Generalizes Phase 21's own "current risk" rule (decision #3) to every
// widget that needs "the current value": while the session is still LIVE
// (non-terminal), "current" means the MOST RECENT item — the video's
// playback position isn't a meaningful reference point yet, since the
// operator is watching a session that's still being written. Once the
// session reaches a terminal status, "current" switches to whatever the
// video player is scrubbed to, via findNearestByTimestamp above.
//
// `items` MUST already be sorted ASCENDING by timestamp_seconds — every
// backend endpoint this is used against either already returns that order
// (GET .../crowd-metrics-timeseries) or the caller sorts client-side first
// (the heatmap list route orders by created_at DESC instead, so
// HeatmapViewer re-sorts its filtered slice before calling this).
export function pickCurrentItem<T extends TimestampedItem>(
  items: T[],
  isTerminal: boolean,
  playbackTimeSeconds: number | null
): T | null {
  if (items.length === 0) return null;
  if (isTerminal && playbackTimeSeconds !== null) {
    return findNearestByTimestamp(items, playbackTimeSeconds);
  }
  return items[items.length - 1];
}
