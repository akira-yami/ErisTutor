from pathlib import Path


def _body() -> str:
    return (Path(__file__).parents[1] / "body" / "UdpListener.DepthPanel.cs").read_text(encoding="utf-8")


def test_v87_identity_navigation_only_self_and_trace() -> None:
    text = _body()
    assert 'AddDepthTab(tabs, "self", "SELF"' in text
    assert 'AddDepthTab(tabs, "trace", "TRACE•"' in text
    assert 'AddDepthTab(tabs, "inner", "INNER//"' not in text


def test_v87_self_is_one_composition_not_nested_dashboard_cards() -> None:
    text = _body()
    self_region = text[text.index("private void RenderIdentitySelfV82") : text.index("private void RenderIdentityTraceV82")]
    assert 'identity_self_portrait_frame.png' in self_region
    assert 'new Vector2(760f, 428f)' in self_region
    assert 'PlaceIdentityControl' in self_region
    assert 'NewIdentityDossierSheet' not in self_region
    assert 'NewIdentityThemeColumnBare' in self_region


def test_v87_trace_uses_asset_native_roles_without_outer_sheet() -> None:
    text = _body()
    trace_region = text[text.index("private void RenderIdentityTraceV82") : text.index("private static void PlaceIdentityControl")]
    assert 'identity_trace_thread_crystal.png' in trace_region
    assert 'identity_trace_timeline_before.png' in trace_region
    assert 'identity_trace_timeline_turn.png' in trace_region
    assert 'identity_trace_timeline_now.png' in trace_region
    assert 'NewIdentityDossierSheet' not in trace_region


def test_v87_secondary_actions_stay_in_header_after_observer_footer_removal() -> None:
    text = _body()
    build = text[text.index("private void BuildDepthPanelUi") : text.index("private void BuildDepthScrollDecoration")]
    assert 'NewIdentityHeaderActionButton' in build
    assert 'ToggleIdentityLearning' in build
    assert 'SendIdentityControl("snapshot")' in build
    assert 'var observerDock' not in build
    assert 'NewFooterButton("Tạm dừng học"' not in build
    assert 'NewFooterButton("Xuất snapshot"' not in build
