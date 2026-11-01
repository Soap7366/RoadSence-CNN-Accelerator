#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export XILINX_XRT=/usr BOARD=Pynq-Z2
exec /usr/local/share/pynq-venv/bin/python3 -B "$root/board/base/tools/boot_pl.py" --manifest "$root/board/base/overlays/cnn_v1/overlay_manifest.json" --state /run/pscnn/boot_state.json --timeout 60
