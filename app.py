"""
Vercel / production WSGI entrypoint.

Loads study_buddy/app.py via importlib so the root module name `app`
does not collide with study_buddy/app.py (which would cause a circular import).
Local workflow remains: `cd study_buddy && python app.py`
"""
import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_STUDY_BUDDY = os.path.join(_ROOT, "study_buddy")
_APP_FILE = os.path.join(_STUDY_BUDDY, "app.py")

# Ensure study_buddy package dir is importable for any relative imports inside app.py
if _STUDY_BUDDY not in sys.path:
    sys.path.insert(0, _STUDY_BUDDY)

_spec = importlib.util.spec_from_file_location("study_buddy_flask_app", _APP_FILE)
_module = importlib.util.module_from_spec(_spec)
sys.modules["study_buddy_flask_app"] = _module
_spec.loader.exec_module(_module)

app = _module.app

try:
    _module.init_db()
except Exception as e:
    print(f"[WARN] init_db from root entrypoint: {e}")

if __name__ == "__main__":
    _module.init_db()
    port = int(os.environ.get("PORT", 5000))
    print("\n[STARTING] Study Buddy is running!")
    app.run(host="0.0.0.0", port=port, debug=False)
