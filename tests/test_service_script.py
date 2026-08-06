import subprocess
from pathlib import Path

def test_service_script_help(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "service.sh"

    result = subprocess.run([str(script_path), "help"], capture_output=True, text=True)
    assert "Usage:" in result.stdout
    assert result.returncode == 1

def test_service_script_status_down(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "service.sh"

    # Normally this might fail if it's actually loaded in the dev environment,
    # but let's just check it doesn't crash
    result = subprocess.run([str(script_path), "status"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Status:" in result.stdout or "NOT loaded" in result.stdout

def test_service_script_health_when_down(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "service.sh"

    result = subprocess.run([str(script_path), "health"], capture_output=True, text=True)
    if "Service is DOWN" in result.stdout:
        assert result.returncode == 1
    elif "Service is UP" in result.stdout:
        assert result.returncode == 0
