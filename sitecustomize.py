from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "apply_live_patch.py"
APP = ROOT / "app.py"
if RUNNER.exists() and APP.exists() and 'SESSION_COOKIE = "sj_session"' not in APP.read_text(encoding="utf-8"):
    try:
        source = RUNNER.read_text(encoding="utf-8")
        source = re.sub(r"\\+", r"\\", source)
        scope = {"__file__": str(RUNNER), "__name__": "__main__"}
        exec(compile(source, str(RUNNER), "exec"), scope, scope)
        print("sitecustomize: live patch applied", flush=True)
    except Exception as exc:
        print("sitecustomize: live patch failed: " + repr(exc), flush=True)
