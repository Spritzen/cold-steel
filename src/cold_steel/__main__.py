"""Entry point: `python -m cold_steel`."""

import sys


def main() -> int:
    # Imported here so `import cold_steel` never pulls in Qt.
    from cold_steel.ui.app import run

    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
