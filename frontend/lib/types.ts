// Shared shapes consumed by more than one dashboard widget (Phase 21/22)
// — kept here instead of redeclared per-component so LiveMonitor and its
// children agree on exactly one definition of "a crowd-metrics snapshot"
// / "a heatmap snapshot."

// Phase 23: exactly the fields CrowdMetricsStatCards.tsx actually reads —
// factored out so the SAME component can render either a full
// CrowdMetricsSnapshotItem (Phase 22's live dashboard) OR an evidence
// package's crowd_metrics_summary (Phase 23's incident detail page, which
// has no frame_number/timestamp_seconds of its own), without duplicating
// the stat-card visual language for the second case.
export interface CrowdMetricsStatFields {
  max_density: number;
  max_pressure: number;
  congested_cell_fraction: number;
  reverse_flow_cell_fraction: number;
  bottleneck_signal_present: boolean;
  density_confidence: number;
  // Final Intelligence phase: moved up from CrowdMetricsSnapshotItem —
  // both the live timeseries snapshot AND an evidence package's
  // crowd_metrics_summary already carry these (CompactCrowdMetricsSummary,
  // backend/app/pipeline/vision_observation.py), so CrowdMetricsStatCards
  // can render a Risk card in either context.
  risk_score: number;
  risk_state: string;
}

export interface CrowdMetricsSnapshotItem extends CrowdMetricsStatFields {
  frame_number: number;
  timestamp_seconds: number;
}

export type HeatmapType = "DENSITY" | "PRESSURE" | "FLOW_CONGESTION" | "RISK" | "PREDICTIVE";

export interface HeatmapSnapshotItem {
  id: string;
  heatmap_type: HeatmapType;
  frame_number: number;
  timestamp_seconds: number;
  file_size_bytes: number;
  created_at: string;
}
