"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import CrowdMetricsStatCards from "@/components/dashboard/CrowdMetricsStatCards";
import { formatDateTime } from "@/lib/formatDate";

import EvidenceImages from "./EvidenceImages";
import IncidentActionButtons from "./IncidentActionButtons";

interface RegionBox {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

interface EvidenceItemData {
  id: string;
  observation_id: string;
  category: string;
  confidence: number;
  evidence_type: string;
  description: string;
  region: RegionBox;
}

interface CrowdMetricsSummary {
  risk_score: number;
  risk_state: string;
  max_density: number;
  max_pressure: number;
  pressure_units_disclaimer: string;
  congested_cell_fraction: number;
  reverse_flow_cell_fraction: number;
  bottleneck_signal_present: boolean;
  density_confidence: number;
}

interface RiskStateSnapshot {
  frame_number: number;
  timestamp_seconds: number;
  state: string;
  risk_score: number;
}

interface PredictiveProjectionSnapshot {
  projected_pressure: number;
  horizon_seconds: number;
  r_squared: number;
}

interface ContradictionData {
  contradiction_type: string;
  description: string;
  resolution_status: string;
}

// Acute-Hazard Trigger Phase: both null for every RISK/FALLBACK/OPERATOR
// package and every pre-this-phase row — present only on an
// ACUTE_HAZARD-triggered EvidencePackage.
interface AcuteHazardSignalData {
  corroborating_signals: string[];
  z_scores: Record<string, number>;
}

interface EventWindowData {
  onset_window_start_seconds: number;
  peak_timestamp_seconds: number;
  context_frame_timestamp_seconds: number | null;
}

interface EvidencePackageData {
  id: string;
  schema_version: string;
  session_id: string;
  frame_number: number;
  timestamp_seconds: number;
  trigger_type: string;
  trigger_reason: string;
  crowd_metrics_summary: CrowdMetricsSummary;
  risk_state_snapshot: RiskStateSnapshot;
  vision_observations_present: boolean;
  predictive_projection_snapshot: PredictiveProjectionSnapshot | null;
  confidence: number;
  binding_constraint: string;
  complete: boolean;
  missing: string[];
  contradictions: ContradictionData[];
  evidence_items: EvidenceItemData[];
  acute_hazard_signal_snapshot: AcuteHazardSignalData | null;
  event_window: EventWindowData | null;
  created_at: string;
}

interface VerificationData {
  id: string;
  passed: boolean;
  confidence_consistency_ok: boolean;
  evidence_grounding_existence_ok: boolean;
  reasoning_consistency_ok: boolean | null;
  contradiction_handling_ok: boolean | null;
  recommendation_consistency_ok: boolean | null;
  unsupported_claims_ok: boolean | null;
  issues_found: string[];
  superseding_decision_id: string | null;
  created_at: string;
}

// Acute-Hazard Trigger Phase: populated for every INCIDENT/WATCH decision
// regardless of trigger type; null for NO_INCIDENT/ABSTAIN and every
// pre-this-phase row.
interface StructuredReportData {
  event_summary: string;
  observed_evidence: string[];
  behavioral_analysis: string;
  spatial_analysis: string;
  temporal_analysis: string;
  crowd_risk_context: string;
}

interface DecisionResultData {
  id: string;
  evidence_package_id: string;
  evidence_cited: string[];
  outcome: string;
  reasoning_summary: string;
  recommendation: string | null;
  recommendation_rationale: string | null;
  projection_narrative: string | null;
  event_classification: string | null;
  structured_report: StructuredReportData | null;
  abstention_reason: string | null;
  confidence: number;
  binding_constraint: string;
  superseded_decision_id: string | null;
  verification: VerificationData | null;
  created_at: string;
}

export interface TimelineEntryData {
  evidence_package: EvidencePackageData;
  decision_result: DecisionResultData;
  correlated_at: string;
}

interface OperatorActionData {
  id: string;
  action_type: string;
  performed_by: string;
  performed_by_email: string;
  notes: string | null;
  created_at: string;
}

export interface IncidentDetailData {
  id: string;
  session_id: string;
  lifecycle_status: string;
  closure_reason: string | null;
  priority: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  latest_recommendation: string | null;
  linked_evidence: TimelineEntryData[];
  operator_actions: OperatorActionData[];
  created_at: string;
  updated_at: string;
}

const OUTCOME_COLORS: Record<string, string> = {
  INCIDENT: "#FF6B35",
  WATCH: "#F2A93B",
  NO_INCIDENT: "#2DD4BF",
  ABSTAIN: "#94A3B8",
};

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-cs-border bg-cs-panel p-5">
      <p className="mb-3 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">{title}</p>
      {children}
    </div>
  );
}

function CheckRow({ label, ok }: { label: string; ok: boolean | null }) {
  const text = ok === null ? "N/A" : ok ? "OK" : "FLAGGED";
  const color = ok === null ? "text-cs-muted" : ok ? "text-cs-teal" : "text-cs-amber";
  return (
    <li className="flex items-center justify-between border-b border-cs-border/50 py-1.5 text-sm last:border-0">
      <span className="text-cs-text">{label}</span>
      <span className={`font-mono text-xs uppercase ${color}`}>{text}</span>
    </li>
  );
}

function TimelineEntryDetail({ entry, accessToken }: { entry: TimelineEntryData; accessToken: string }) {
  const { evidence_package: pkg, decision_result: decision } = entry;
  const outcomeColor = OUTCOME_COLORS[decision.outcome] ?? "#94A3B8";

  return (
    <div className="flex flex-col gap-6">
      {/* Evidence */}
      <SectionCard title="Evidence">
        <p className="text-sm text-cs-text">
          <span className="text-cs-muted">Trigger:</span> {pkg.trigger_type} — {pkg.trigger_reason}
        </p>
        <p className="mt-1 text-sm text-cs-text">
          <span className="text-cs-muted">Frame:</span> #{pkg.frame_number} (t=
          {pkg.timestamp_seconds.toFixed(2)}s)
        </p>
        {!pkg.complete && (
          <div className="mt-3 border border-cs-amber/40 bg-cs-amber/10 p-3">
            <p className="font-mono text-xs tracking-[0.1em] text-cs-amber uppercase">
              Incomplete evidence
            </p>
            <ul className="mt-1 list-inside list-disc text-sm text-cs-text">
              {pkg.missing.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </div>
        )}
        {pkg.contradictions.length > 0 && (
          <div className="mt-3 border border-cs-amber/40 bg-cs-amber/10 p-3">
            <p className="font-mono text-xs tracking-[0.1em] text-cs-amber uppercase">
              Contradictions — {pkg.contradictions.length}
            </p>
            <ul className="mt-1 flex flex-col gap-1 text-sm text-cs-text">
              {pkg.contradictions.map((c, i) => (
                <li key={i}>
                  <span className="text-cs-muted">{c.contradiction_type}:</span> {c.description}{" "}
                  <span className="font-mono text-[10px] text-cs-muted uppercase">
                    ({c.resolution_status})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="mt-3 text-xs text-cs-muted">
          Evidence confidence {(pkg.confidence * 100).toFixed(0)}% — {pkg.binding_constraint}
        </p>
      </SectionCard>

      {/* Acute-Hazard Trigger Phase: baseline -> onset -> peak -> aftermath
          timeline, present only for an ACUTE_HAZARD-triggered package.
          onset_window_start_seconds is deliberately shown as the START of
          a WINDOW, not a fabricated precise instant (see
          evidence_package.py's EventWindow docstring) — peak_timestamp is
          the one exact value here (the triggering frame itself). */}
      {pkg.event_window && (
        <SectionCard title="Event Timeline">
          <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-cs-text">
            <span className="border border-cs-border px-2 py-1 text-cs-muted uppercase">
              Onset window from t={pkg.event_window.onset_window_start_seconds.toFixed(2)}s
            </span>
            <span className="text-cs-muted">→</span>
            <span className="border border-cs-amber px-2 py-1 text-cs-amber uppercase">
              Peak (trigger) t={pkg.event_window.peak_timestamp_seconds.toFixed(2)}s
            </span>
          </div>
          {pkg.event_window.context_frame_timestamp_seconds !== null && (
            <p className="mt-2 text-xs text-cs-muted">
              &ldquo;Before&rdquo; comparison frame captured at t=
              {pkg.event_window.context_frame_timestamp_seconds.toFixed(2)}s
            </p>
          )}
          {pkg.acute_hazard_signal_snapshot && (
            <div className="mt-3 border-t border-cs-border pt-3">
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Corroborating deterministic signals
              </p>
              <ul className="mt-1 flex flex-wrap gap-2">
                {pkg.acute_hazard_signal_snapshot.corroborating_signals.map((signal) => (
                  <li
                    key={signal}
                    className="border border-cs-teal px-2 py-0.5 font-mono text-[10px] text-cs-teal uppercase"
                  >
                    {signal.replace(/_/g, " ")}{" "}
                    {pkg.acute_hazard_signal_snapshot!.z_scores[signal] !== undefined &&
                      `(z=${pkg.acute_hazard_signal_snapshot!.z_scores[signal].toFixed(1)})`}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SectionCard>
      )}

      {/* Crowd Metrics */}
      <SectionCard title="Crowd Metrics">
        <CrowdMetricsStatCards current={pkg.crowd_metrics_summary} />
        <p className="mt-3 text-xs text-cs-muted">{pkg.crowd_metrics_summary.pressure_units_disclaimer}</p>
        <p className="mt-2 text-sm text-cs-text">
          Risk at capture: <span className="text-cs-muted">{pkg.risk_state_snapshot.state}</span> (
          {pkg.risk_state_snapshot.risk_score.toFixed(1)})
        </p>
        {pkg.predictive_projection_snapshot && (
          <div className="mt-3 border-t border-cs-border pt-3">
            <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
              Predictive Projection
            </p>
            <p className="mt-1 text-sm text-cs-text">
              Projected pressure {pkg.predictive_projection_snapshot.projected_pressure.toFixed(1)}{" "}
              in {pkg.predictive_projection_snapshot.horizon_seconds.toFixed(0)}s (R²=
              {pkg.predictive_projection_snapshot.r_squared.toFixed(2)})
            </p>
            {decision.projection_narrative && (
              <p className="mt-1 text-sm text-cs-muted italic">{decision.projection_narrative}</p>
            )}
          </div>
        )}
      </SectionCard>

      {/* VLM Observations */}
      <SectionCard title="VLM Observation(s)">
        {!pkg.vision_observations_present ? (
          <p className="text-sm text-cs-amber">VLM call failed — no observations available.</p>
        ) : pkg.evidence_items.length === 0 ? (
          <p className="text-sm text-cs-muted">
            VLM analyzed the scene and found no qualifying observations.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {/* Index badge matches the SAME 1-indexed number drawn on that
                observation's bounding box in EvidenceImages.tsx below —
                same array, same order, the only persistent way to tell
                "which box is which card" once there is more than one
                observation. */}
            {pkg.evidence_items.map((item, index) => (
              <li key={item.id} className="border border-cs-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex items-center gap-2 font-mono text-xs tracking-[0.1em] text-cs-teal uppercase">
                    <span className="flex h-4 w-4 items-center justify-center bg-cs-amber text-[10px] font-bold text-cs-bg">
                      {index + 1}
                    </span>
                    {item.category}
                  </span>
                  <span className="font-mono text-[10px] text-cs-muted uppercase">
                    {item.evidence_type} · {(item.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                <p className="mt-1 text-sm text-cs-text">{item.description}</p>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      {/* Representative Frame / ROI */}
      <SectionCard title="Representative Frame / ROI">
        <EvidenceImages
          evidencePackageId={pkg.id}
          accessToken={accessToken}
          observations={pkg.vision_observations_present ? pkg.evidence_items : []}
        />
      </SectionCard>

      {/* Decision Intelligence */}
      <SectionCard title="Decision Intelligence">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-mono text-sm uppercase" style={{ color: outcomeColor }}>
            {decision.outcome}
          </p>
          {/* Acute-Hazard Trigger Phase: applies to every INCIDENT/WATCH
              decision regardless of trigger type — an ordinary crowd-crush
              incident shows CROWD_CRUSH here too, not a blank. */}
          {decision.event_classification && (
            <span className="border border-cs-amber px-2 py-0.5 font-mono text-[10px] text-cs-amber uppercase">
              {decision.event_classification.replace(/_/g, " ")}
            </span>
          )}
        </div>
        {/* Structured report (required only when outcome=INCIDENT) takes
            precedence when present — reasoning_summary alone remains the
            source of truth for WATCH/NO_INCIDENT/ABSTAIN and every
            pre-this-phase row. */}
        {decision.structured_report ? (
          <div className="mt-3 flex flex-col gap-3">
            <p className="text-sm text-cs-text">{decision.structured_report.event_summary}</p>
            <div>
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Observed Evidence
              </p>
              <ul className="mt-1 list-inside list-disc text-sm text-cs-text">
                {decision.structured_report.observed_evidence.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Behavioral Analysis
              </p>
              <p className="mt-1 text-sm text-cs-text">{decision.structured_report.behavioral_analysis}</p>
            </div>
            <div>
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Spatial Analysis
              </p>
              <p className="mt-1 text-sm text-cs-text">{decision.structured_report.spatial_analysis}</p>
            </div>
            <div>
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Temporal Analysis
              </p>
              <p className="mt-1 text-sm text-cs-text">{decision.structured_report.temporal_analysis}</p>
            </div>
            <div>
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Crowd-Risk Context
              </p>
              <p className="mt-1 text-sm text-cs-text">{decision.structured_report.crowd_risk_context}</p>
            </div>
          </div>
        ) : (
          <p className="mt-2 text-sm text-cs-text">{decision.reasoning_summary}</p>
        )}
        {decision.evidence_cited.length > 0 && (
          <p className="mt-2 text-xs text-cs-muted">
            Evidence cited: {decision.evidence_cited.join(", ")}
          </p>
        )}
        {decision.recommendation && (
          <p className="mt-3 text-sm text-cs-text">
            <span className="text-cs-muted">Recommendation:</span> {decision.recommendation}
            {decision.recommendation_rationale && (
              <span className="text-cs-muted"> — {decision.recommendation_rationale}</span>
            )}
          </p>
        )}
        {decision.abstention_reason && (
          <p className="mt-3 text-sm text-cs-muted">
            Abstention reason: {decision.abstention_reason}
          </p>
        )}
        {decision.superseded_decision_id && (
          <p className="mt-3 border border-cs-amber/40 bg-cs-amber/10 p-2 text-xs text-cs-amber">
            This decision was SUPERSEDED by verification —{" "}
            {decision.superseded_decision_id}.
          </p>
        )}
      </SectionCard>

      {/* Confidence — the FIRST place this traceability field is genuinely
          surfaced to a human (built since Phase 16/17 specifically for
          this purpose). */}
      <SectionCard title="Confidence">
        <p className="text-3xl font-bold text-cs-text">{(decision.confidence * 100).toFixed(0)}%</p>
        <p className="mt-2 text-sm text-cs-muted">{decision.binding_constraint}</p>
      </SectionCard>

      {/* Verification */}
      {decision.verification && (
        <SectionCard title="Verification">
          <p
            className={`font-mono text-sm uppercase ${
              decision.verification.passed ? "text-cs-teal" : "text-cs-amber"
            }`}
          >
            {decision.verification.passed ? "PASSED" : "FLAGGED"}
          </p>
          <ul className="mt-3">
            <CheckRow label="Confidence consistency" ok={decision.verification.confidence_consistency_ok} />
            <CheckRow
              label="Evidence grounding existence"
              ok={decision.verification.evidence_grounding_existence_ok}
            />
            <CheckRow label="Reasoning consistency" ok={decision.verification.reasoning_consistency_ok} />
            <CheckRow
              label="Contradiction handling"
              ok={decision.verification.contradiction_handling_ok}
            />
            <CheckRow
              label="Recommendation consistency"
              ok={decision.verification.recommendation_consistency_ok}
            />
            <CheckRow label="No unsupported claims" ok={decision.verification.unsupported_claims_ok} />
          </ul>
          {decision.verification.issues_found.length > 0 && (
            <div className="mt-3 border border-cs-amber/40 bg-cs-amber/10 p-3">
              <p className="font-mono text-xs tracking-[0.1em] text-cs-amber uppercase">
                Issues found
              </p>
              <ul className="mt-1 list-inside list-disc text-sm text-cs-text">
                {decision.verification.issues_found.map((issue, i) => (
                  <li key={i}>{issue}</li>
                ))}
              </ul>
            </div>
          )}
        </SectionCard>
      )}
    </div>
  );
}

export default function IncidentTimeline({
  incident,
  accessToken,
  isAdmin,
}: {
  incident: IncidentDetailData;
  accessToken: string;
  isAdmin: boolean;
}) {
  const router = useRouter();
  // Default to the MOST RECENT entry (last, timeline is chronologically
  // ascending per Resolution 1) — the entry most likely to explain the
  // incident's current state.
  const [selectedIndex, setSelectedIndex] = useState(
    Math.max(0, incident.linked_evidence.length - 1)
  );
  const selected = incident.linked_evidence[selectedIndex] ?? null;

  function handleActionComplete() {
    // Unlike Phase 22's IncidentsList.tsx (a leaf inside a live-monitoring
    // client tree, where a full refresh would lose video playback
    // position), THIS page is a forensic/audit view with no such state to
    // preserve — a full server-component refetch is exactly right, so the
    // lifecycle badges AND the Operator Actions history below both reflect
    // the real new state.
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="lg:w-72 lg:flex-none">
          <p className="mb-3 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            Timeline ({incident.linked_evidence.length})
          </p>
          {incident.linked_evidence.length === 0 ? (
            <p className="text-sm text-cs-muted">No timeline entries.</p>
          ) : (
            <ul className="flex flex-col gap-2 border-l border-cs-border pl-4">
              {incident.linked_evidence.map((entry, index) => {
                const color = OUTCOME_COLORS[entry.decision_result.outcome] ?? "#94A3B8";
                const isSelected = index === selectedIndex;
                return (
                  <li key={entry.decision_result.id}>
                    <button
                      onClick={() => setSelectedIndex(index)}
                      className={`block w-full border px-3 py-2 text-left transition-colors ${
                        isSelected
                          ? "border-cs-teal bg-cs-panel"
                          : "border-cs-border bg-transparent hover:border-cs-muted"
                      }`}
                    >
                      <p className="font-mono text-[10px] text-cs-muted">
                        {formatDateTime(entry.correlated_at)}
                      </p>
                      <p className="mt-1 font-mono text-xs uppercase" style={{ color }}>
                        {entry.decision_result.outcome}
                      </p>
                      <p className="mt-1 line-clamp-2 text-xs text-cs-muted">
                        {entry.evidence_package.trigger_reason}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex-1">
          {selected ? (
            <TimelineEntryDetail entry={selected} accessToken={accessToken} />
          ) : (
            <p className="text-sm text-cs-muted">No timeline entries to display.</p>
          )}
        </div>
      </div>

      {/* Operator Actions */}
      <SectionCard title="Operator Actions">
        <IncidentActionButtons
          incidentId={incident.id}
          lifecycleStatus={incident.lifecycle_status}
          acknowledged={incident.acknowledged}
          isAdmin={isAdmin}
          onActionComplete={handleActionComplete}
        />
        {incident.operator_actions.length === 0 ? (
          <p className="mt-4 text-sm text-cs-muted">No operator actions recorded yet.</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-2 border-t border-cs-border pt-4">
            {incident.operator_actions.map((action) => (
              <li key={action.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-cs-text">
                  <span className="font-mono text-xs tracking-[0.1em] text-cs-teal uppercase">
                    {action.action_type}
                  </span>{" "}
                  by <span className="text-cs-muted">{action.performed_by_email}</span>
                  {action.notes && <span className="text-cs-muted"> — {action.notes}</span>}
                </span>
                <span className="text-xs text-cs-muted">
                  {formatDateTime(action.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
