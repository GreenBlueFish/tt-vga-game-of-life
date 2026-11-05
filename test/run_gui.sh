#!/usr/bin/env bash
# Launch the interactive GUI (cocotb MODULE=gui) using the Windows Python
# that has cocotb installed. See gui.py for usage.
set -e
export PATH="/usr/bin:/c/iverilog/bin:$PYBIN:$PYSCRIPTS:$PATH"
# cd to this script's own directory (robust regardless of launch cwd).
cd "$(dirname "$0")"
make -B MODULE=gui
