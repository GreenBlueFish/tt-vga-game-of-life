# Tests

This directory contains a [cocotb](https://docs.cocotb.org/) testbench that
verifies the VGA timing and the Game-of-Life control interface of
`tt_um_vga_interface`.

## What is tested

VGA timing (always running, independent of the simulation):

- `test_reset` - leaves reset with resolvable (non-X) outputs, a cleared grid,
  and the unused bidirectional pins tied off as inputs.
- `test_hsync_period` - one horizontal line is exactly 800 pixel clocks.
- `test_vsync_period` - one full frame is exactly 525 x 800 clocks.

Control interface (serial load + step/speed):

- `test_serial_load` - a 16-bit pattern shifted in through `load_en`/`load_data`
  lands in the grid correctly (snake order).
- `test_load_holds_without_step` - the grid does not advance across vblanks
  while no step occurs.
- `test_step_one_generation` - `speed=0`: one `step` rising edge advances
  exactly one generation; a blinker flips orientation (checked against a
  Python Conway reference).
- `test_speed_multiple_generations` - `speed=1` computes 2 generations per
  step, `speed=2` computes 4.
- `test_display_reflects_grid` - a loaded live cell is drawn (non-black) on the
  screen, and the grid is unchanged while displaying.

## Runtime

Generations are committed during vertical blanking, so each `step`/`speed`
test simulates several full 640x480 frames (420,000 clocks per frame). The
full suite takes a few minutes under Icarus Verilog - this is expected, not a
hang.

## Running (Linux / macOS)

```shell
pip install -r requirements.txt
make -B
```

To view waveforms afterwards:

```shell
gtkwave tb.vcd
```

## Running on Windows

cocotb's Makefile needs Unix shell tools (`make`, `tr`, `sh`) and **cannot
handle spaces in the project path** - and this project lives under a path with
a space (`UW 2B`). So **`make` will not work when run directly** from this
folder in PowerShell; you will get either `make: command not found` or
`No targets specified`.

Use the launcher at the project root instead. It copies the sources to a
space-free staging dir, sets up the MSYS2 + Icarus Verilog + Python toolchain,
and runs the flow for you:

```powershell
# from the project root (one level up from test/)
powershell -ExecutionPolicy Bypass -File run_gui.ps1            # interactive GUI
powershell -ExecutionPolicy Bypass -File run_gui.ps1 -Module test   # the test suite
powershell -ExecutionPolicy Bypass -File run_gui.ps1 -SelfTest 3    # GUI smoke test
```

If the tool paths differ on your machine, edit the variables at the top of
`run_gui.ps1` (`$Bash`, `$IverilogWin`, `$PyWin`, `$PyScripts`). The launcher
uses the Python that has cocotb installed (not necessarily your active venv).

Requirements on Windows: [MSYS2](https://www.msys2.org/) (for `make` and Unix
tools), [Icarus Verilog](https://bleyer.org/icarus/), and a Tk-capable Python
with cocotb installed (`pip install -r requirements.txt`).

## Quick timing check without cocotb

`tb_selfcheck.v` verifies the VGA timing with Icarus Verilog alone:

```shell
iverilog -g2012 -o tb_selfcheck.vvp -s tb_selfcheck \
    tb_selfcheck.v ../src/project.v ../src/vga_timing.v ../src/golchip.v
vvp tb_selfcheck.vvp
```

## Interactive GUI (`gui.py`)

An interactive window for driving `project.v` by hand and watching the VGA
output. It runs as a cocotb test and drives the real RTL simulation.

```shell
# Linux / macOS, from this test/ directory:
make -B MODULE=gui

# Windows, from the project root:
powershell -ExecutionPolicy Bypass -File run_gui.ps1
```

The window gives you:

- **Toggle switches** for all 8 `ui_in` pins (step, run, load_en, load_data,
  speed[1:0], and the two unused bits) plus `rst_n`. Flip them like physical
  DIP switches; changes feed straight into the simulation.
- **Serial-load pattern**: type a 16-bit grid (cell 0 is the rightmost bit)
  and click to shift it in through the snake chain.
- **Pulse step**: issues one `step` rising edge and runs 2^speed generations.
- **VGA preview**: a live view of what the monitor would show, decoded from
  the core grid and coloured exactly as `project.v` drives the pins.
- **Capture real VGA frame**: scans `uo_out` pixel-by-pixel across one real
  640x480 frame to prove the actual pin output (slow; on demand).
- A readout of the raw `uo_out` byte, decoded RGB + sync, the grid, and busy.

Close the window to end the test. Setting `GUI_SELFTEST=<N>` runs a scripted
load+step and auto-closes (used to smoke-test the GUI without a human).

## Gate-level simulation

After the GDS GitHub Action has produced `gate_level_netlist.v`, copy it here
and run:

```shell
make -B GATES=yes
```
