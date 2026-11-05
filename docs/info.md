<!---
This file is used to generate your project datasheet. Please fill in the
information below and delete any unused sections.
-->

## How it works

This project runs a **4×4 Conway's Game of Life** on-chip and displays it on a
**640x480 @ 60 Hz** VGA monitor via the
[TinyVGA Pmod](https://tinytapeout.com/specs/pinouts/). You write the starting
grid in **serially** (one bit per clock), then advance the simulation with a
**step** control; `speed` sets how many generations each step computes.

It is built from three source files:

| Module | Role |
|--------|------|
| `vga_timing`          | Free-running H/V counters. Produces `hsync`, `vsync`, `display_on`, a one-cycle `vblank_start` pulse, and the beam position (`hpos`, `vpos`). |
| `golchip.v` (core)    | The Game of Life engine: a sequencer, a 4×4 grid of cells (each a compaction shift register implementing the Life rule), a serial load chain, and a read mux that returns the one cell the beam is drawing. |
| `tt_um_vga_interface` | Top module. Wires the two together, derives `row`/`col` from the beam position, turns the selected cell into colour, and maps the result onto the TinyVGA Pmod pins. |

The clock must run at the VGA pixel clock of **25.175 MHz**. Sync pulses are
**active-low** (negative polarity), as the 640x480@60 mode and the TinyVGA
Pmod expect.

Generations are computed **only during vertical blanking** (triggered by the
`vblank_start` pulse) as a 12-cycle routine, so the monitor never sees a
half-updated grid. Between steps the grid holds and the VGA keeps displaying
the current, stable state.

### Control interface (`ui_in`)

| `ui_in` bit | Signal | Meaning |
|-------------|--------|---------|
| [7]   | step      | rising edge computes 2^speed generations |
| [6]   | run       | reserved (unused; step is the only trigger) |
| [5]   | load_en   | serial load enable |
| [4]   | load_data | serial data bit |
| [3:2] | —         | unused (tie low) |
| [1:0] | speed     | generations computed per step: 2^speed (1, 2, 4, 8) |

**Writing the grid (serial load).** Hold `load_en` high and present bits on
`load_data`. Each clock the bit shifts one position along the 16-cell snake
chain (row 0 left→right, row 1 right→left, …). After 16 clocks the whole 4×4
grid is loaded; the last bit shifted in lands in cell 0.

**Advancing.** A rising edge on `step` computes 2^speed generations, each
committed during vertical blanking (so a burst spans 2^speed frames). The grid
only changes in response to a step — it never free-runs.

The 4×4 grid fills the screen (each cell 160×120 px): live cells white, dead
cells near-black, with thin grey separators. Reset (`rst_n`) clears the grid.

### Timing (640x480 @ 60 Hz)

| Axis | Visible | Front porch | Sync | Back porch | Total |
|------|---------|-------------|------|------------|-------|
| Horizontal (px)   | 640 | 16 | 96 | 48 | 800 |
| Vertical (lines)  | 480 | 10 | 2  | 33 | 525 |

Frame rate = 25.175 MHz / (800 x 525) ≈ 59.94 Hz.

## How to test

1. Set the clock input to **25,175,000 Hz**.
2. Connect a **TinyVGA Pmod** to the dedicated output header (`uo_out`) and a
   VGA monitor to the Pmod.
3. Reset (`rst_n` low then high) to clear the grid (all dead).
4. **Load a pattern.** Set `speed` as desired. Hold `load_en=1` and drive the
   16-bit pattern on `load_data`, one bit per clock, for 16 clocks (snake
   order; the last bit lands in cell 0). Release `load_en`.
5. **Step.** Pulse `step` 0→1. The engine computes 2^speed generations,
   updating the display at each vertical blank. A 3-in-a-row "blinker" flips
   between horizontal and vertical each generation.
6. Hold or re-pulse `step` to keep advancing; raise `speed` to fast-forward
   multiple generations per step.

To run the simulation suite (cocotb + Icarus Verilog):

```shell
cd test
pip install -r requirements.txt
make -B
```

The tests verify the VGA timing (800-clock lines, 96-clock HSync pulse,
525×800 frames), serial loading, that the grid holds without a step, that one
step advances exactly one generation (verified against a Conway reference),
and that `speed` computes 2/4 generations per step. Note: because generations
are vblank-synced, the step/speed tests each simulate several full frames and
take a few minutes.

## External hardware

- [TinyVGA Pmod](https://github.com/mole99/tiny-vga) (or any Pmod wired to the
  same pinout) connected to the dedicated output pins.
- A VGA monitor.

### Output pinout (TinyVGA Pmod)

| `uo_out` bit | Signal | Notes |
|--------------|--------|-------|
| 0 | R1    | red MSB |
| 1 | G1    | green MSB |
| 2 | B1    | blue MSB |
| 3 | VSync | active low |
| 4 | R0    | red LSB |
| 5 | G0    | green LSB |
| 6 | B0    | blue LSB |
| 7 | HSync | active low |

Each colour channel is 2 bits, giving 64 possible colours.
