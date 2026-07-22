import importlib.util
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
QLIB_DIR = ROOT / "qlib"
if str(QLIB_DIR) not in sys.path:
    sys.path.insert(0, str(QLIB_DIR))


def load_qlib_module(name: str) -> ModuleType:
    path = QLIB_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
