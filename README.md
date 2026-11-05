# Tiny Tapeout VGA Game of Life

A **4×4 Conway's Game of Life** that runs on-chip and displays on a
**640x480 @ 60 Hz** VGA monitor through the standard
[TinyVGA Pmod](https://tinytapeout.com/specs/pinouts/). You write the starting
grid in **serially** through the input pins, then advance the simulation with
a **step** control; a **speed** setting fast-forwards several generations per
step.

The structure and build/test flow follow the Tiny Tapeout Verilog template and
the [UW-ASIC/Dino](https://github.com/UW-ASIC/Dino) reference project.

---

## Repository layout

```
.
├── info.yaml              # Tiny Tapeout project metadata + pinout
├── src/
│   ├── config.json        # OpenLane hardening config (default; do not edit)
│   ├── project.v          # tt_um_vga_interface — top module (VGA + core glue)
│   ├── vga_timing.v       # 640x480@60 sync/timing generator + vblank pulse
│   └── golchip.v          # Game of Life core (sequencer, grid, cell, readmux)
├── test/
│   ├── Makefile           # cocotb + Icarus Verilog driver
│   ├── tb.v               # Verilog testbench harness (DUT instantiation)
│   ├── test.py            # cocotb tests (timing, serial load, step/speed)
│   ├── tb_selfcheck.v     # standalone self-checking TB (no cocotb needed)
│   ├── requirements.txt   # Python deps (cocotb, pytest)
│   └── README.md
└── docs/
    └── info.md            # project datasheet
```

---

## Module overview

| Module | Role |
|--------|------|
| `vga_timing`          | H/V counters. Produces `hsync`, `vsync`, `display_on`, a one-cycle `vblank_start` pulse, and the beam position (`hpos`, `vpos`). |
| `golchip_core`        | Game of Life engine (in `golchip.v`): a sequencer, a 4×4 grid of cells, a serial load chain, and a read mux that returns the one cell the beam is drawing. |
| `tt_um_vga_interface` | Top module. Derives `row`/`col` from the beam position, turns the selected cell into colour, and maps colour + sync onto the TinyVGA Pmod pins. |

---

## How the VGA side works

VGA scans the screen one pixel at a time, left to right, top to bottom. The
beam is only "on" inside the visible 640x480 window; the rest of each line and
frame is blanking, during which the colour must be black and a sync pulse
tells the monitor to retrace.

`vga_timing` implements this with two counters driven by the pixel clock:

- **`hpos` (0–799)** advances every clock; wraps after 800 clocks (one line).
- **`vpos` (0–524)** advances once per line; wraps after 525 lines (one frame).

From those it derives `display_on` (inside the visible area), the active-low
`hsync`/`vsync` pulses, and **`vblank_start`** — a one-cycle pulse on the first
clock of vertical blanking that the Game of Life core uses to time its updates.

### 640x480 @ 60 Hz timing

| Axis | Visible | Front porch | Sync | Back porch | Total |
|------|---------|-------------|------|------------|-------|
| Horizontal (px)   | 640 | 16 | 96 | 48 | **800** |
| Vertical (lines)  | 480 | 10 | 2  | 33 | **525** |

- Pixel clock: **25.175 MHz**
- Frame rate: 25.175 MHz / (800 × 525) ≈ **59.94 Hz**

---

## How the Game of Life core works

The core (`golchip.v`) holds a 4×4 = 16-cell grid. Each cell is a small
compaction shift register that counts its live neighbours and applies the
Conway rule (survive on 2–3 neighbours, born on exactly 3), with a dead border
around the grid. A sequencer runs one generation as a 12-cycle routine.

**Generations are computed only during vertical blanking.** The sequencer
starts a generation on the `vblank_start` pulse and commits the result at the
end of the 12-cycle routine, still inside blanking — so the monitor never sees
a half-updated grid. Between updates the grid holds and the VGA keeps showing
the current, stable state.

**The grid only advances in response to a step.** There is no free-running
mode; the simulation is fully under your control via the `step` pin.

---

## Control interface (`ui_in`)

| `ui_in` bit | Signal | Meaning |
|-------------|--------|---------|
| [7]   | step      | rising edge computes 2^speed generations |
| [6]   | run       | reserved (unused; step is the only trigger) |
| [5]   | load_en   | serial load enable |
| [4]   | load_data | serial data bit |
| [3:2] | —         | unused (tie low) |
| [1:0] | speed     | generations computed per step: 2^speed (1, 2, 4, 8) |

### Writing the grid (serial load)

The grid is written **serially** — there is no address bus. Hold `load_en`
high and present bits on `load_data`; each clock the bit shifts one position
along a 16-cell **snake** (boustrophedon) chain that threads the grid row by
row, alternating direction (row 0 left→right, row 1 right→left, and so on).

Shift 16 bits (one per clock) to fill the whole grid; the **last** bit shifted
in lands in cell 0 (top-left), and earlier bits occupy later chain positions.
Reset (`rst_n`) clears the grid to all-dead.

The exact stream-slot → cell mapping (verified in `test/test.py`, `SNAKE`) is:

```
 slot:  0   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15
 cell: 12  13  14  15  11  10   9   8   4   5   6   7   3   2   1   0
```

where cell index = row*4 + col (row 0 top, col 0 left). The test and docs use
this table so you can construct an exact pattern.

### Advancing the simulation

A rising edge on `step` computes **2^speed** generations:

| speed | generations per step |
|-------|----------------------|
| 0 | 1 |
| 1 | 2 |
| 2 | 4 |
| 3 | 8 |

Each generation commits during a vertical blank, so a burst of N generations
spans N frames and you see it animate forward. Because updates are
vblank-synced, the displayed image is always a complete, stable generation.

---

## Output: TinyVGA Pmod (`uo_out`)

```
          uo_out
 bit  7    6    5    4    3    2    1    0
     HS   B0   G0   R0   VS   B1   G1   R1
```

| `uo_out` | Signal | Notes |
|----------|--------|-------|
| [0] | R1    | red   MSB |
| [1] | G1    | green MSB |
| [2] | B1    | blue  MSB |
| [3] | VSync | active low |
| [4] | R0    | red   LSB |
| [5] | G0    | green LSB |
| [6] | B0    | blue  LSB |
| [7] | HSync | active low |

Each channel is 2 bits, so the palette is 64 colours. The grid fills the
screen (each cell 160×120 px): live cells white, dead cells near-black, with
thin grey separators. The output register adds a one-cycle pipeline stage for
clean VGA edges (sync and colour are delayed together, so the picture stays
aligned). The bidirectional pins (`uio_*`) are unused (`uio_oe = 0`).

To use it on hardware: set the clock to 25.175 MHz, connect a TinyVGA Pmod to
the output header, and plug a VGA monitor into the Pmod.

---

## Building and testing

### cocotb test suite (matches CI)

Requires [Icarus Verilog](https://steveicarus.github.io/iverilog/) and the
Python packages in `test/requirements.txt`.

```shell
cd test
pip install -r requirements.txt
make -B
```

> **Windows note:** cocotb's Makefile needs Unix shell tools and does not
> tolerate spaces in the path. Run it under MSYS2 / Git-Bash from a directory
> whose full path has no spaces.

> **Runtime note:** the step/speed tests are vblank-synced, so each simulates
> several full 640×480 frames (420k clocks each). The suite takes a few
> minutes — that is expected, not a hang.

The suite checks:

- VGA timing: 800-clock lines, 96-clock HSync pulse, 525×800 frames, resolvable
  outputs after reset, unused bidir pins tied off.
- **Serial load**: a shifted-in pattern lands in the grid correctly.
- **Hold**: the grid does not advance across vblanks without a step.
- **Step = one generation**: a blinker flips orientation each step (checked
  against a Conway reference).
- **Speed**: `speed=1` computes 2 generations per step, `speed=2` computes 4.

### Quick timing check without cocotb

`test/tb_selfcheck.v` verifies the VGA timing with Icarus Verilog alone:

```shell
iverilog -g2012 -o tb_selfcheck.vvp -s tb_selfcheck \
    test/tb_selfcheck.v src/project.v src/vga_timing.v src/golchip.v
vvp tb_selfcheck.vvp
```

It prints `ALL TESTS PASSED` on success.

---

## Credits

- Game of Life core (`golchip.v`): provided design, integrated here.
- VGA timing adapted from the Tiny Tapeout Verilog project template.
- TinyVGA Pmod pinout per the [Tiny Tapeout pinout spec](https://tinytapeout.com/specs/pinouts/).
- Reference project: [UW-ASIC/Dino](https://github.com/UW-ASIC/Dino).
