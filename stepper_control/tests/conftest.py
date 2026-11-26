import importlib.util
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load the firmware's controller.py by path. Putting stepper_control/ on sys.path
# would let the firmware's code.py shadow the stdlib `code` module.
_spec = importlib.util.spec_from_file_location("controller", os.path.join(root, "controller.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["controller"] = _mod
_spec.loader.exec_module(_mod)

sys.path.insert(0, os.path.join(root, "host"))  # feeder_host
