# tests/test_appearance.py - Verify browser settings and navigation without network or cloud access.


from pathlib import Path
import shutil
import subprocess

import pytest


# Exercise real navigation, settings discovery and appearance through native control events
@pytest.mark.parametrize("script", ["appearance_ui.mjs", "shell_ui.cjs"])
def test_browser_settings_contract(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser settings DOM contract")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, str(root / "tests" / script)], cwd=root,
                   check=True, capture_output=True, text=True, timeout=20)
