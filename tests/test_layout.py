"""The core never imports Qt, so it can be tested and timed without a window."""

import subprocess
import sys

QT_FREE = ["cold_steel", "cold_steel.core.jobs", "cold_steel.store.paths", "cold_steel.paradox"]


def test_core_packages_do_not_import_qt() -> None:
    # A fresh interpreter, so modules loaded by other tests don't hide an import.
    code = (
        "import sys\n"
        f"for m in {QT_FREE!r}: __import__(m)\n"
        "print(any(name.startswith('PySide6') for name in sys.modules))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "False"
