# Event 0002

## Event
- timestamp: 13.77s
- frame_number: 413
- trigger_id: ACUTE_HAZARD-0002
- trigger_reason: acute hazard signals corroborated: motion_energy, flow_divergence

## Deterministic evidence
- corroborating_signals: ['motion_energy', 'flow_divergence']
- z_scores: {
  "scene_change": 1.8046014037929667,
  "motion_energy": 13.581945662271705,
  "flow_divergence": 7.350893221617347,
  "detection_count_delta": 0.24458241393262514
}
- raw_values: {}
- spatial_active_cell_fraction: N/A (localization_grid is not persisted, unavailable in full-chain mode)
- spatial_largest_component_fraction: N/A (localization_grid is not persisted, unavailable in full-chain mode)
- roi_bbox (pixel space): (0.0, 400.0, 82.28571428571429, 520.0)

## VLM evidence
- vlm_call_succeeded: True
- observation_categories: ['VISIBLE_OBSTRUCTION']
- evidence complete: True (missing: [])
- contradictions: []
- exact images sent and full observation detail: see evidence_package.json

## Decision layer
- outcome: ABSTAIN
- abstention_reason: ACUTE_HAZARD trigger fired but VLM evidence does not corroborate with an acute-hazard-consistent observation category (found: ['VISIBLE_OBSTRUCTION']; routine crowd-management categories alone are insufficient grounds for an acute-hazard-sourced incident)
- event_classification: None
- incident_created: False
- diagnosis_stage: EVIDENCE_CONSISTENCY_GATE — Deterministic abstention (no LLM outcome was reached): ACUTE_HAZARD trigger fired but VLM evidence does not corroborate with an acute-hazard-consistent observation category (found: ['VISIBLE_OBSTRUCTION']; routine crowd-management categories alone are insufficient grounds for an acute-hazard-sourced incident)
- full Reasoner output: see decision_result.json

## Operator interpretation
The deterministic AcuteHazardDetector flagged frame 413 (t=13.77s) because 2 signal(s) corroborated together: motion_energy, flow_divergence. The VLM reported observation categories: ['VISIBLE_OBSTRUCTION']. The system abstained deterministically: ACUTE_HAZARD trigger fired but VLM evidence does not corroborate with an acute-hazard-consistent observation category (found: ['VISIBLE_OBSTRUCTION']; routine crowd-management categories alone are insufficient grounds for an acute-hazard-sourced incident) What remains uncertain: this reflects only what THIS video's pixels produced through the current, unmodified pipeline — it is not a judgment about whether the underlying real-world event was actually hazardous, and it is not validated against any labeled ground truth unless this sample's manifest entry explicitly supplies one.

## Artifacts
- montage.jpg
- before.jpg / trigger.jpg / after.jpg / roi.jpg
- heatmap_density.jpg
- heatmap_pressure.jpg
- heatmap_flow_congestion.jpg
- heatmap_risk.jpg
- heatmap_predictive.jpg