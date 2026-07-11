#!/usr/bin/env python3
"""Regenerate the shipped *universal* signed "Play Flow Crate" shortcut.

macOS-only (needs the `shortcuts` CLI). Run this whenever build_workflow changes:

    PYTHONPATH=src python scripts/build_universal_shortcut.py

It writes src/flowcrate/static/play-flow-crate-universal.shortcut — a shortcut
signed with `--mode anyone` (which chains to Apple's generic internal CA, so it
carries no personal identity) whose URL/token are placeholders the user edits on
their device. Never bake a real host/token in: this file is public.
"""

from pathlib import Path

from flowcrate.shortcut import UNIVERSAL_TOKEN, UNIVERSAL_URL, signed_shortcut

OUT = Path(__file__).resolve().parent.parent / "src/flowcrate/static/play-flow-crate-universal.shortcut"


def main():
    data = signed_shortcut(UNIVERSAL_URL, UNIVERSAL_TOKEN)
    OUT.write_bytes(data)
    print(f"Wrote {OUT} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
