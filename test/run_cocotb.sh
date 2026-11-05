#!/usr/bin/env bash
# Helper to run the cocotb suite under msys2 bash while using the
# Windows Python install that has cocotb 1.9.2 installed.
set -e
export PATH="/usr/bin:/c/iverilog/bin:$PYBIN:$PYSCRIPTS:$PATH"
echo "iverilog: $(command -v iverilog)"
echo "python:   $(command -v python)"
python --version
echo "cocotb:   $(cocotb-config --version)"
make -B
