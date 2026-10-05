"""Launch the desktop editor from any working directory."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "repository.json"
UTF8 = "utf-8"
configuration = json.loads(CONFIG_FILE.read_text(encoding=UTF8))
directory = ROOT / configuration["primary"]
raise SystemExit(subprocess.call([sys.executable, "run.py"], cwd=directory))
