from experiments.emergence.acceptance import PDF_ACCEPTANCE


def test_pdf_acceptance_thresholds_are_version_controlled() -> None:
    assert PDF_ACCEPTANCE["persistence"].minimums["effect_retention_ratio"] == 0.70
    assert PDF_ACCEPTANCE["counterfactual"].minimums["history_classifier_auroc"] == 0.75
    assert PDF_ACCEPTANCE["hidden_state_prediction"].minimums["history_brier_delta"] == 0.03
    assert PDF_ACCEPTANCE["resistance"].maximums["one_turn_shift_sd"] == 0.25
    assert PDF_ACCEPTANCE["transfer"].maximums["transfer_ratio"] == 0.80
    assert PDF_ACCEPTANCE["ablation"].maximums["ablation_collateral_change"] == 0.10
