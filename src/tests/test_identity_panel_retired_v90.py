from pathlib import Path


def _body() -> str:
    return (Path(__file__).parents[1] / "body" / "UdpListener.cs").read_text(encoding="utf-8")


def test_identity_panel_is_not_constructed_or_exposed_in_context_menu() -> None:
    text = _body()
    assert 'Name = "DepthPanel"' not in text
    assert 'BuildDepthPanelUi();' not in text
    assert 'new Button { Name = "DepthButton" }' not in text
    assert 'ConfigureContextMenuButton(_contextDepthButton, "Identity Runtime"' not in text
    assert '_contextDepthButton.Pressed += OnContextDepthPressed' not in text


def test_identity_runtime_backend_capture_is_kept() -> None:
    text = _body()
    assert 'TryCaptureIdentityRuntimeBlock(raw);' in text
    assert '_latestIdentityRuntimeJson' in text
