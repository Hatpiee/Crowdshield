"use client";

import type { ReactNode } from "react";

import { RISK_STATE_COLORS } from "@/lib/riskColors";
import type { CrowdMetricsStatFields } from "@/lib/types";

// density.py's own confidence tiers: 1.0 (full KDE fit, the implicit
// default), 0.85 (VORONOI_UNAVAILABLE_CONFIDENCE), 0.5
// (HIGH_DISAGREEMENT_CONFIDENCE), 0.4 (TOO_FEW_POINTS_CONFIDENCE). 0.85 is
// the first tier below the full-confidence default, so anything at or
// below it means SOME real degradation occurred — chosen as the cutoff for
// this card's muted/dashed treatment, per this project's "never hide
// degraded confidence" principle (Phase 16/17).
const DENSITY_LOW_CONFIDENCE_THRESHOLD = 0.85;

function StatCard({
  label,
  muted,
  children,
}: {
  label: string;
  muted?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={`border bg-cs-panel p-4 ${
        muted ? "border-dashed border-cs-muted/50" : "border-cs-border"
      }`}
    >
      <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">{label}</p>
      {children}
    </div>
  );
}

const CARD_LABELS = ["Risk", "Density", "Pressure", "Congestion", "Flow"];

export default function CrowdMetricsStatCards({
  current,
}: {
  current: CrowdMetricsStatFields | null;
}) {
  if (!current) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {CARD_LABELS.map((label) => (
          <StatCard key={label} label={label}>
            <p className="mt-2 text-sm text-cs-muted">No data yet</p>
          </StatCard>
        ))}
      </div>
    );
  }

  const densityLowConfidence = current.density_confidence < DENSITY_LOW_CONFIDENCE_THRESHOLD;
  // Resolution 3: Congestion = congested_cell_fraction alone. Flow = BOTH
  // reverse_flow_cell_fraction AND bottleneck_signal_present together —
  // both are flow-DYNAMICS signals, distinct from Congestion's static
  // occupancy. Raw spatial flow-field data (divergence/curl) is
  // deliberately NOT exposed at this summary level — the FLOW_CONGESTION
  // heatmap already shows it visually (arrows over a congestion base
  // layer). A simple "is this signal present at all" binary (rather than
  // an arbitrary magnitude cutoff neither field has a natural universal
  // threshold for) drives the teal/amber color here — documented judgment
  // call, consistent with the "never hide a degraded/abnormal signal"
  // principle applied to density_confidence above.
  const congestionActive = current.congested_cell_fraction > 0;
  const flowActive = current.reverse_flow_cell_fraction > 0 || current.bottleneck_signal_present;

  const riskColor = RISK_STATE_COLORS[current.risk_state];

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      <StatCard label="Risk">
        <p className="mt-2 text-2xl font-bold" style={{ color: riskColor }}>
          {current.risk_score.toFixed(1)}
        </p>
        <p className="mt-1 text-xs" style={{ color: riskColor }}>
          {current.risk_state} · 0-100
        </p>
      </StatCard>

      <StatCard label="Density" muted={densityLowConfidence}>
        <p
          className={`mt-2 text-2xl font-bold ${
            densityLowConfidence ? "text-cs-muted" : "text-cs-text"
          }`}
        >
          {current.max_density.toFixed(3)}
        </p>
        <p className="mt-1 text-xs text-cs-muted">
          confidence {(current.density_confidence * 100).toFixed(0)}%
          {densityLowConfidence ? " · degraded" : ""}
        </p>
      </StatCard>

      {/* Pressure: no magnitude-based coloring — px-space units (Phase 9's
          own disclosure) have no defensible universal danger cutoff at
          this summary-card level without the full spatial context the
          Pressure heatmap image already provides (Resolution 4: its own
          units disclaimer is baked into that image, not repeated here). */}
      <StatCard label="Pressure">
        <p className="mt-2 text-2xl font-bold text-cs-text">{current.max_pressure.toFixed(1)}</p>
        <p className="mt-1 text-xs text-cs-muted">px-space (see heatmap)</p>
      </StatCard>

      <StatCard label="Congestion">
        <p
          className={`mt-2 text-2xl font-bold ${
            congestionActive ? "text-cs-amber" : "text-cs-teal"
          }`}
        >
          {(current.congested_cell_fraction * 100).toFixed(1)}%
        </p>
        <p className="mt-1 text-xs text-cs-muted">of grid cells</p>
      </StatCard>

      <StatCard label="Flow">
        <p className={`mt-2 text-2xl font-bold ${flowActive ? "text-cs-amber" : "text-cs-teal"}`}>
          {(current.reverse_flow_cell_fraction * 100).toFixed(1)}%
        </p>
        <p className="mt-1 text-xs text-cs-muted">
          reverse flow{current.bottleneck_signal_present ? " · bottleneck" : ""}
        </p>
      </StatCard>
    </div>
  );
}
