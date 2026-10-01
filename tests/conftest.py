import os

# GUI tests run headless unless asked otherwise (`QT_QPA_PLATFORM=wayland make test`).
os.environ["QT_QPA_PLATFORM"] = os.environ.get("COLD_STEEL_TEST_QPA", "offscreen")
