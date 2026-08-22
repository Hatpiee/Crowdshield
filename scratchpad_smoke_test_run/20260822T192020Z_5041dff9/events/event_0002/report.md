# Event 0002

## Event
- timestamp: 7.00s
- frame_number: 210
- trigger_id: ACUTE_HAZARD-0002
- trigger_reason: acute hazard signals corroborated: motion_energy, flow_divergence, scene_change

## Deterministic evidence
- corroborating_signals: ['motion_energy', 'flow_divergence', 'scene_change']
- z_scores: {
  "scene_change": 9.679376114419716,
  "motion_energy": 64.99249200242109,
  "flow_divergence": 75.54934402559128,
  "detection_count_delta": -0.43036167841885853
}
- raw_values: {}
- spatial_active_cell_fraction: N/A (localization_grid is not persisted, unavailable in full-chain mode)
- spatial_largest_component_fraction: N/A (localization_grid is not persisted, unavailable in full-chain mode)
- roi_bbox (pixel space): (411.42857142857144, 440.0, 534.8571428571429, 560.0)

## VLM evidence
- vlm_call_succeeded: False
- observation_categories: []
- evidence complete: False (missing: ['vision_observations'])
- contradictions: []
- exact images sent and full observation detail: see evidence_package.json

## Decision layer
- outcome: ABSTAIN
- abstention_reason: evidence is materially incomplete: missing=['vision_observations']
- event_classification: None
- incident_created: False
- diagnosis_stage: VLM_UNAVAILABLE — The VLM call itself failed/was unavailable for this trigger — EvidencePackage.vision_observations_present=False, missing_evidence includes 'vision_observations'. No semantic interpretation occurred; this is an availability issue, not a detector or evidence-quality issue.
- full Reasoner output: see decision_result.json

## Operator interpretation
The deterministic AcuteHazardDetector flagged frame 210 (t=7.00s) because 3 signal(s) corroborated together: motion_energy, flow_divergence, scene_change. The VLM call failed, so no semantic interpretation of this moment exists. The system abstained deterministically: evidence is materially incomplete: missing=['vision_observations'] What remains uncertain: this reflects only what THIS video's pixels produced through the current, unmodified pipeline — it is not a judgment about whether the underlying real-world event was actually hazardous, and it is not validated against any labeled ground truth unless this sample's manifest entry explicitly supplies one.

## Artifacts
- montage.jpg
- before.jpg / trigger.jpg / after.jpg / roi.jpg
- heatmap_density.jpg
- heatmap_pressure.jpg
- heatmap_flow_congestion.jpg
- heatmap_risk.jpg
- heatmap_predictive.jpg