from __future__ import annotations

from pathlib import Path


def _body() -> str:
    return (Path(__file__).parents[1] / "body" / "UdpListener.DepthPanel.cs").read_text(encoding="utf-8")


def test_v83_identity_is_one_panel_with_self_trace_tabs() -> None:
    text = _body()
    assert 'AddDepthTab(tabs, "self", "SELF"' in text
    assert 'AddDepthTab(tabs, "inner", "INNER//"' not in text
    assert 'AddDepthTab(tabs, "trace", "TRACE•"' in text
    assert 'ERIS / IDENTITY_01' in text
    assert 'private const string IdentityAssetRoot = "res://asset/identy/";' in text
    assert 'RenderIdentityInnerV82' not in text


def test_v83_normal_identity_ui_avoids_metric_bar_dashboard() -> None:
    text = _body()
    self_tab = text[text.index("private void RenderIdentitySelfV82") : text.index("private void RenderIdentityTraceV82")]
    trace_tab = text[text.index("private void RenderIdentityTraceV82") : text.index("private PanelContainer NewIdentityDossierSheet")]
    assert "AddMetricBar" not in self_tab
    assert "AddMetricBar" not in trace_tab
    assert '"DESIRE"' in self_tab
    assert '"TENSION"' in self_tab
    assert '"CORE"' in self_tab
    assert '"THEN"' in trace_tab
    assert '"NOW"' in trace_tab


def test_identity_panel_does_not_read_retired_gemma_shards_or_label_fallback_as_observer() -> None:
    # Snapshot identity vẫn dùng được khi payload/cache cũ còn giữ shard/comment;
    # renderer không đọc chúng hoặc gắn nhãn Gemma cho headline fallback.
    text = _body()
    assert "observer_comment" not in text
    assert "thought_shards" not in text
    assert "GEMMA // OBSERVER" not in text
    assert "RefreshIdentityObserverV82" not in text
    assert "ReadGemmaThoughtShardV82" not in text
    headline = text[text.index("private string BuildIdentitySelfHeadlineV82") : text.index("private string BuildIdentityFormTagsV82")]
    assert 'Obj(root, "latest_observation")' in headline
    assert 'Obj(observation, "self_interpretation")' in headline
    assert 'return BuildIdentityHeadline(root);' in headline
