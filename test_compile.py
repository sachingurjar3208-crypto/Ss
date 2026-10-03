#!/usr/bin/env python3
"""Quick syntax check for the three modified files."""
import py_compile
import sys

files = [
    'card_db.py',
    'card_image.py',
    'cardmaker_cog.py',
]

failed = False
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"✓ {f}")
    except py_compile.PyCompileError as e:
        print(f"✗ {f}: {e}")
        failed = True

sys.exit(1 if failed else 0)
