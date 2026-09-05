#!/usr/bin/env python3
"""STEP 1 helper: probe a source video with ffprobe.

Usage:
    python scripts/probe_video.py input_videos/NN.mp4

Prints a JSON summary (duration, resolution, fps, has_audio, subtitle
streams) to stdout so the pipeline can decide the subtitle strategy.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ffcommon import probe_summary, eprint  # noqa: E402


def main(argv):
    if len(argv) != 2:
        eprint("usage: probe_video.py <video>")
        return 2
    path = argv[1]
    if not os.path.exists(path):
        eprint("error: file not found: %s" % path)
        return 1
    try:
        summary = probe_summary(path)
    except Exception as exc:  # noqa: BLE001 - report and fail cleanly
        eprint("error: probe failed: %s" % exc)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
