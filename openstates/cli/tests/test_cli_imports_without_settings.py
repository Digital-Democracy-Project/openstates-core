"""`os-text-extract` must load with no Django settings: its commands call init_django() themselves.

A module-level import of anything that loads the Django models (the importers do) makes every command, the nightly
archive included, fail with ImproperlyConfigured before init_django() runs. The test suite sets
DJANGO_SETTINGS_MODULE for itself, so only a fresh interpreter without it shows the failure.
"""

import os
import subprocess
import sys


def test_text_extract_imports_without_django_settings():
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    result = subprocess.run(
        [sys.executable, "-c", "import openstates.cli.text_extract"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-2000:]
