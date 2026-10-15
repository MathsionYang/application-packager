"""CLI integration checks; QA modes never build or install software."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_help_can_run_from_another_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--data-dir" in result.stdout
    assert "--smoke-test" in result.stdout


def test_screenshot_mode_renders_without_starting_a_build(tmp_path):
    pictures = tmp_path / "截图 目录"
    data = tmp_path / "隔离数据"
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--smoke-test",
         "--data-dir", str(data), "--screenshot-dir", str(pictures)],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    screenshot = pictures / "workbench.png"
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert screenshot.stat().st_size > 10_000
    assert not list(tmp_path.rglob("*.exe"))
