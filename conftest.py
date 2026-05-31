# Root conftest.py
# Exclude old smoke-test scripts from pytest collection —
# they are not pytest-compatible (standalone __main__ scripts).
collect_ignore = [
    "tests/test_pdf_tools.py",
    "tests/test_vision_tools.py",
]
