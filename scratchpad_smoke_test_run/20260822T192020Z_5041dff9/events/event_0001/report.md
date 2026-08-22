# Event 0001

## Event
- timestamp: 2.00s
- frame_number: 60
- trigger_id: ACUTE_HAZARD-0001
- trigger_reason: acute hazard signals corroborated: flow_divergence, detection_count_delta

## Deterministic evidence
- corroborating_signals: ['flow_divergence', 'detection_count_delta']
- z_scores: {
  "scene_change": 2.7215347813743387,
  "motion_energy": 0.9217374490631494,
  "flow_divergence": 7.564577269229688,
  "detection_count_delta": 4.821158915995747
}
- raw_values: {}
- spatial_active_cell_fraction: N/A (localization_grid is not persisted, unavailable in full-chain mode)
- spatial_largest_component_fraction: N/A (localization_grid is not persisted, unavailable in full-chain mode)
- roi_bbox (pixel space): (493.7142857142858, 160.0, 576.0, 280.0)

## VLM evidence
- vlm_call_succeeded: False
- observation_categories: []
- evidence complete: False (missing: ['vision_observations'])
- contradictions: []
- exact images sent and full observation detail: see evidence_package.json

## Decision layer
- outcome: ABSTAIN
- abstention_reason: confidence=0.400 is at or below DECISION_CONFIDENCE_FLOOR=0.400
- event_classification: None
- incident_created: False
- diagnosis_stage: VLM_UNAVAILABLE — The VLM call itself failed/was unavailable for this trigger — EvidencePackage.vision_observations_present=False, missing_evidence includes 'vision_observations'. No semantic interpretation occurred; this is an availability issue, not a detector or evidence-quality issue.
- full Reasoner output: see decision_result.json

## Operator interpretation
The deterministic AcuteHazardDetector flagged frame 60 (t=2.00s) because 2 signal(s) corroborated together: flow_divergence, detection_count_delta. The VLM call failed, so no semantic interpretation of this moment exists. The system abstained deterministically: confidence=0.400 is at or below DECISION_CONFIDENCE_FLOOR=0.400 What remains uncertain: this reflects only what THIS video's pixels produced through the current, unmodified pipeline — it is not a judgment about whether the underlying real-world event was actually hazardous, and it is not validated against any labeled ground truth unless this sample's manifest entry explicitly supplies one.

## Artifacts
- montage.jpg
- before.jpg / trigger.jpg / after.jpg / roi.jpg
- heatmap_density.jpg
- heatmap_pressure.jpg
- heatmap_flow_congestion.jpg
- heatmap_risk.jpg
- heatmap_predictive.jpg