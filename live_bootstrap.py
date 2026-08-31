from pathlib import Path
import os
import re
import sys

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "apply_live_patch_v2.py"
APP = ROOT / "app.py"
if RUNNER.exists() and APP.exists() and 'SESSION_COOKIE = "sj_session"' not in APP.read_text(encoding="utf-8"):
    try:
        source = RUNNER.read_text(encoding="utf-8")
        source = re.sub(r"\\+", r"\\", source)
        scope = {"__file__": str(RUNNER), "__name__": "__main__"}
        exec(compile(source, str(RUNNER), "exec"), scope, scope)
        print("live_bootstrap: patch applied; restarting", flush=True)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as exc:
        print("live_bootstrap: patch failed: " + repr(exc), flush=True)
