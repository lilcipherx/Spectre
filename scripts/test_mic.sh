#!/usr/bin/env bash
# Quick sanity checks for the audio chain: list devices, record 3 s from the
# configured ALSA device, show VU meter peaks.
#
# Usage:
#     scripts/test_mic.sh                # uses $SPECTRE_AUDIO_DEVICE or plughw:1,0
#     scripts/test_mic.sh plughw:2,0
set -euo pipefail

DEVICE="${1:-${SPECTRE_AUDIO_DEVICE:-plughw:1,0}}"
OUT="$(mktemp --suffix=.wav)"

echo ">> arecord -l"
arecord -l || true
echo

echo ">> recording 3 s from $DEVICE to $OUT"
arecord -D "$DEVICE" -f S16_LE -r 16000 -c 1 -d 3 "$OUT"

echo ">> peak levels (should reach at least -20 dBFS when someone is talking)"
sox "$OUT" -n stats 2>&1 | grep -E "Max(imum|) amplitude|Peak level|RMS level" || \
    echo "   (install sox for peak stats: sudo apt install sox)"

echo ">> playback (Ctrl-C to stop)"
aplay "$OUT" || true
