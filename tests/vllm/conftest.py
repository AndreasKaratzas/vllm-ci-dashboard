"""Ensure scripts/vllm is importable as 'vllm.ci.*' from tests/vllm/.

Without this, Python resolves 'vllm' to tests/vllm/ (this package)
instead of scripts/vllm/ where the CI modules live.
"""
import sys
from pathlib import Path

# Insert scripts/ at the front of sys.path so 'from vllm.ci.models import ...'
# resolves to scripts/vllm/ci/models.py, not tests/vllm/.
_scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
