from __future__ import annotations

from pathlib import Path


def test_generated_launcher_exits_without_holding_a_console() -> None:
    build_script = Path(__file__).resolve().parents[2] / "_fullbuild.py"
    source = build_script.read_text(encoding="utf-8")

    assert "run_eris.vbs" in source
    assert "start \"Eris Runtime\" cmd /k" not in source
    assert "\"pause\"," not in source
    assert "exit /b 0" in source
    assert "pythonw.exe" in source


def test_manager_start_eris_uses_silent_launcher() -> None:
    manager = Path(__file__).resolve().parents[1] / "manager_gui.py"
    source = manager.read_text(encoding="utf-8")

    assert 'Action("Runtime", "Start Eris", "run_eris.vbs", kind="vbs"' in source
    assert "def run_vbs(" in source
