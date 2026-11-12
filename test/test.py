# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0
#
# Tests for the VGA Game-of-Life integration (serial load + step/speed).

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles

# TinyVGA Pmod bit positions on uo_out.
#   R1=0 G1=1 B1=2 VSync=3 R0=4 G0=5 B0=6 HSync=7
HSYNC_BIT = 7
VSYNC_BIT = 3

import os

# VGA timing. These MUST match src/vga_timing.v. When the RTL is built with
# -DSIM_FAST_FRAME the frame is shrunk; set GUI_FAST_FRAME=1 so the test uses
# the same (much smaller) numbers and runs in seconds instead of minutes.
if os.environ.get("GUI_FAST_FRAME", "0") == "1":
    H_DISPLAY, H_FRONT, H_SYNC, H_BACK = 40, 2, 4, 2
    V_DISPLAY, V_FRONT, V_SYNC, V_BACK = 20, 1, 2, 1
else:
    H_DISPLAY, H_FRONT, H_SYNC, H_BACK = 640, 16, 96, 48
    V_DISPLAY, V_FRONT, V_SYNC, V_BACK = 480, 10, 2, 33
H_TOTAL = H_DISPLAY + H_FRONT + H_SYNC + H_BACK
V_TOTAL = V_DISPLAY + V_FRONT + V_SYNC + V_BACK

# Gate-level simulation (make GATES=yes). Synthesis flattens the design, so
# internal paths such as dut.user_project.u_core / u_timing don't exist in the
# netlist. Tests that read them are skipped at gate level; the port-only tests
# (sync timing + test_ports_only_step) still run.
GL = os.environ.get("GATES", "") == "yes"

# ui_in control bit layout (serial load):
#   [7] step, [6] run(unused), [5] load_en, [4] load_data, [1:0] speed
def ui(step=0, run=0, load_en=0, load_data=0, speed=0):
    return ((step & 1) << 7) | ((run & 1) << 6) | ((load_en & 1) << 5) \
        | ((load_data & 1) << 4) | (speed & 0x3)


def hsync(dut):
    return (int(dut.uo_out.value) >> HSYNC_BIT) & 1


def vsync(dut):
    return (int(dut.uo_out.value) >> VSYNC_BIT) & 1


def cells(dut):
    return int(dut.user_project.u_core.cells.value)


def start_clock(dut):
    # 25.175 MHz pixel clock ~= 39.72 ns. Use 40 ns for simulation.
    cocotb.start_soon(Clock(dut.clk, 40, units="ns").start())


async def reset_dut(dut):
    dut.ena.value = 1
    dut.ui_in.value = ui()
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


# --- Bounded 4x4 Conway reference (dead borders), row-major bit = r*4+c ---
def conway_next(grid):
    out = 0
    for r in range(4):
        for c in range(4):
            n = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < 4 and 0 <= cc < 4:
                        n += (grid >> (rr * 4 + cc)) & 1
            alive = (grid >> (r * 4 + c)) & 1
            nxt = 1 if (n == 3 or (alive and n == 2)) else 0
            out |= nxt << (r * 4 + c)
    return out


async def serial_load(dut, target):
    """Load `target` (16-bit, bit i -> cell i) via the serial snake chain.

    The chain shifts one cell per clock while load_en is high. We discover
    the stream order once by probing, but here we simply shift 16 bits and
    rely on the fact (verified in RTL) that after 16 clocks `cells` holds
    whatever stream we fed. To hit an exact target we brute-force the stream
    by shifting the bits in the snake order the hardware expects.
    """
    # Snake order: the cell that ends up at index `idx` was shifted in at
    # time-slot given by the inverse of the chain. Empirically (see repo
    # history) the stream order that yields cells==pattern is:
    #   slot t -> cell = SNAKE[t]
    SNAKE = [12, 13, 14, 15, 11, 10, 9, 8, 4, 5, 6, 7, 3, 2, 1, 0]
    await FallingEdge(dut.clk)
    dut.ui_in.value = ui(load_en=1)
    for t in range(16):
        bit = (target >> SNAKE[t]) & 1
        dut.ui_in.value = ui(load_en=1, load_data=bit)
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
    dut.ui_in.value = ui()
    await RisingEdge(dut.clk)


async def wait_vblank(dut):
    """Advance to the first clock of vertical blanking."""
    # vblank_start is internal; detect the vsync edge region instead by
    # waiting for the internal pulse via the timing block.
    while int(dut.user_project.u_timing.vblank_start.value) != 1:
        await RisingEdge(dut.clk)


async def do_step(dut, speed=0):
    """Produce a rising edge on step at the given speed, then wait for the
    burst (2^speed generations) to complete."""
    dut.ui_in.value = ui(step=0, speed=speed)
    await ClockCycles(dut.clk, 3)
    # Rising edge on step; hold it high through the burst.
    dut.ui_in.value = ui(step=1, speed=speed)

    # Wait until the core registers the request and goes busy. The burst
    # begins at the next vblank, so this can take up to ~one frame.
    busy_seen = False
    for _ in range(2 * V_TOTAL * H_TOTAL):
        await RisingEdge(dut.clk)
        if int(dut.user_project.u_core.busy.value) == 1:
            busy_seen = True
            break
    assert busy_seen, "core never went busy after step"

    # Wait until the whole burst (2^speed generations) completes.
    done = False
    for _ in range(V_TOTAL * H_TOTAL * (2 ** speed) + 20 * H_TOTAL):
        await RisingEdge(dut.clk)
        if int(dut.user_project.u_core.busy.value) == 0:
            done = True
            break
    assert done, f"burst (speed={speed}, {2**speed} gens) did not finished in time"

    dut.ui_in.value = ui(step=0, speed=speed)
    await RisingEdge(dut.clk)


# ---------------------------------------------------------------------------
# VGA timing (always running, independent of the sim).
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_reset(dut):
    start_clock(dut)
    await reset_dut(dut)
    await ClockCycles(dut.clk, 100)
    assert dut.uo_out.value.is_resolvable, "uo_out has X/Z after reset"
    assert int(dut.uio_oe.value) == 0
    assert int(dut.uio_out.value) == 0
    assert cells(dut) == 0, "grid should be cleared by reset"


@cocotb.test()
async def test_hsync_period(dut):
    start_clock(dut)
    await reset_dut(dut)

    async def next_fall():
        prev = hsync(dut)
        while True:
            await RisingEdge(dut.clk)
            cur = hsync(dut)
            if prev == 1 and cur == 0:
                return
            prev = cur

    await next_fall()
    count, prev = 0, 0
    while True:
        await RisingEdge(dut.clk)
        count += 1
        cur = hsync(dut)
        if prev == 1 and cur == 0:
            break
        prev = cur
    assert count == H_TOTAL, f"hsync period {count} != {H_TOTAL}"


@cocotb.test()
async def test_vsync_period(dut):
    start_clock(dut)
    await reset_dut(dut)

    async def next_fall():
        prev = vsync(dut)
        while True:
            await RisingEdge(dut.clk)
            cur = vsync(dut)
            if prev == 1 and cur == 0:
                return
            prev = cur

    await next_fall()
    count, prev = 0, 0
    while True:
        await RisingEdge(dut.clk)
        count += 1
        cur = vsync(dut)
        if prev == 1 and cur == 0:
            break
        prev = cur
    expected = V_TOTAL * H_TOTAL
    assert count == expected, f"vsync period {count} != {expected}"


# ---------------------------------------------------------------------------
# Serial load.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_serial_load(dut):
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0000_0111_0000  # blinker, cells 4,5,6
    await serial_load(dut, pat)
    assert cells(dut) == pat, f"loaded {cells(dut):#06x} != {pat:#06x}"


@cocotb.test(skip=GL)
async def test_load_holds_without_step(dut):
    """After loading, the grid holds as long as no step occurs."""
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0010_0110_0100
    await serial_load(dut, pat)
    assert cells(dut) == pat
    # Idle across at least one vblank (where a generation would start if one
    # were pending) - the grid must not change without a step.
    seen_vblank = 0
    for _ in range(6 * V_TOTAL * H_TOTAL):
        await RisingEdge(dut.clk)
        if int(dut.user_project.u_timing.vblank_start.value) == 1:
            seen_vblank += 1
            if seen_vblank >= 5:
                break
    assert seen_vblank >= 5, "did not observe vblanks"
    assert cells(dut) == pat, "grid advanced without a step"


# ---------------------------------------------------------------------------
# Stepping and speed.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_step_one_generation(dut):
    """speed=0: one step advances exactly one generation (blinker flips)."""
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0000_0111_0000      # horizontal blinker
    await serial_load(dut, pat)

    exp1 = conway_next(pat)          # should be vertical
    await do_step(dut, speed=0)
    assert cells(dut) == exp1, \
        f"after 1 gen {cells(dut):#06x} != {exp1:#06x}"

    exp2 = conway_next(exp1)         # back to horizontal
    await do_step(dut, speed=0)
    assert cells(dut) == exp2, \
        f"after 2 gens {cells(dut):#06x} != {exp2:#06x}"


@cocotb.test(skip=GL)
async def test_speed_multiple_generations(dut):
    """speed=1 computes 2 generations per step; speed=2 computes 4."""
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0010_0110_0100
    await serial_load(dut, pat)

    # speed=1 -> 2 generations.
    exp = conway_next(conway_next(pat))
    await do_step(dut, speed=1)
    assert cells(dut) == exp, \
        f"speed1 (2 gens) {cells(dut):#06x} != {exp:#06x}"

    # speed=2 -> 4 more generations.
    exp2 = pat_after(cells(dut), 4)
    await do_step(dut, speed=2)
    assert cells(dut) == exp2, \
        f"speed2 (4 gens) {cells(dut):#06x} != {exp2:#06x}"


def pat_after(grid, n):
    for _ in range(n):
        grid = conway_next(grid)
    return grid


@cocotb.test(skip=GL)
async def test_display_reflects_grid(dut):
    """The displayed colour tracks the loaded grid: a live cell shows
    non-black inside its cell region, a dead cell shows the dead colour."""
    start_clock(dut)
    await reset_dut(dut)
    # Load only cell 0 (top-left) alive.
    pat = 0b0000_0000_0000_0001
    await serial_load(dut, pat)

    # Scan one frame; record whether any white (all colour bits high in the
    # top-left cell region) appears, which indicates a live cell was drawn.
    # We just confirm colour is non-black somewhere in the visible area
    # (separators + the live cell), and that the grid is unchanged.
    saw_colour = False
    colour_mask = 0xFF & ~((1 << HSYNC_BIT) | (1 << VSYNC_BIT))
    # advance to a frame start
    while int(dut.user_project.u_timing.vpos.value) != 0:
        await RisingEdge(dut.clk)
    for _ in range(8 * H_TOTAL):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) & colour_mask) != 0:
            saw_colour = True
    assert saw_colour, "no colour drawn for a loaded grid"
    assert cells(dut) == pat, "grid changed during display scan"



# ===========================================================================
# Extended tests
# ===========================================================================
#
# Most of these use GUI_FAST_FRAME=1 (with -DSIM_FAST_FRAME on the RTL) to
# run in seconds. On the full 640x480 frame a single frame is 420,000 clocks,
# so the randomised / multi-pattern tests automatically scale their counts
# down when FAST is False.

import random

FAST = os.environ.get("GUI_FAST_FRAME", "0") == "1"
FRAME = V_TOTAL * H_TOTAL

# Cell geometry. MUST match src/project.v (CELL_W/CELL_H/SEPW).
N_GRID = 4
CELL_W = H_DISPLAY // N_GRID
CELL_H = V_DISPLAY // N_GRID
SEPW = 1 if FAST else 4

# Colour bits on uo_out (everything except the two sync bits).
COLOUR_MASK = 0xFF & ~((1 << HSYNC_BIT) | (1 << VSYNC_BIT))
# {r,g,b} -> uo_out = {hsync, b0, g0, r0, vsync, b1, g1, r1}
COLOUR_LIVE = 0x77   # r=g=b=11 (white)
COLOUR_DEAD = 0x40   # b=01     (near-black blue, only B0 set)
COLOUR_SEP  = 0x70   # r=g=b=01 (dim grey separator)

# Same order as serial_load's SNAKE (stream slot t -> cell index).
SNAKE_ORDER = [12, 13, 14, 15, 11, 10, 9, 8, 4, 5, 6, 7, 3, 2, 1, 0]


def colour(dut):
    return int(dut.uo_out.value) & COLOUR_MASK


class CommitMonitor:
    """Background monitor for the core's commit pulse (gen_tick).

    Records how many generations were committed and the beam line (vpos) at
    each one. Optionally also flags any change of `cells` while the beam is
    in the visible area (which would be screen tearing).
    """

    def __init__(self, dut, watch_tearing=False):
        self.dut = dut
        self.count = 0
        self.vpos = []
        self.watch_tearing = watch_tearing
        self.tear_events = []
        self._task = cocotb.start_soon(self._run())

    async def _run(self):
        core = self.dut.user_project.u_core
        timing = self.dut.user_project.u_timing
        prev_cells = int(core.cells.value)
        while True:
            await RisingEdge(self.dut.clk)
            if int(core.gen_tick.value) == 1:
                self.count += 1
                self.vpos.append(int(timing.vpos.value))
            if self.watch_tearing:
                cur = int(core.cells.value)
                if cur != prev_cells and int(timing.display_on.value) == 1:
                    self.tear_events.append(
                        (int(timing.hpos.value), int(timing.vpos.value)))
                prev_cells = cur

    def stop(self):
        self._task.kill()


async def wait_until(dut, cond, max_cycles, msg):
    for _ in range(max_cycles):
        await RisingEdge(dut.clk)
        if cond():
            return
    assert False, msg


async def wait_not_busy(dut, max_cycles, msg="core stayed busy"):
    await wait_until(
        dut, lambda: int(dut.user_project.u_core.busy.value) == 0,
        max_cycles, msg)


# ---------------------------------------------------------------------------
# Port-only helpers (no internal hierarchy) - usable for gate-level sims.
# ---------------------------------------------------------------------------

async def wait_frame_origin(dut):
    """Return on the clock where uo_out shows pixel (hpos=0, vpos=0).

    Uses only the vsync pin: vsync (active low) rises at the start of the
    first back-porch line, and the visible frame starts V_BACK lines later.
    All of uo_out is registered together, so colour and sync stay aligned.
    """
    while vsync(dut) == 1:
        await RisingEdge(dut.clk)
    while vsync(dut) == 0:
        await RisingEdge(dut.clk)
    await ClockCycles(dut.clk, V_BACK * H_TOTAL)


async def cells_from_pixels(dut):
    """Reconstruct the 16-bit grid from the VGA output pins alone.

    Samples the centre pixel of every cell during one frame. Centre pixels
    are far from separators, so a clock or two of sampling skew is harmless.
    Raises if a cell centre is neither the live nor the dead colour.
    """
    await wait_frame_origin(dut)
    t = 0
    grid = 0
    for r in range(N_GRID):
        for c in range(N_GRID):
            h = c * CELL_W + (CELL_W + SEPW) // 2
            v = r * CELL_H + (CELL_H + SEPW) // 2
            target = v * H_TOTAL + h
            await ClockCycles(dut.clk, target - t)
            t = target
            col = colour(dut)
            if col == COLOUR_LIVE:
                grid |= 1 << (r * N_GRID + c)
            else:
                assert col == COLOUR_DEAD, \
                    f"cell ({r},{c}) centre colour {col:#04x} is neither " \
                    f"live {COLOUR_LIVE:#04x} nor dead {COLOUR_DEAD:#04x}"
    return grid


# ---------------------------------------------------------------------------
# 1. Random patterns against the reference model.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_random_patterns(dut):
    """Random 4x4 grids, stepped at speed 0 and 1, match conway_next."""
    start_clock(dut)
    await reset_dut(dut)
    rng = random.Random(int(os.environ.get("GOL_SEED", "298")))
    n_patterns = 30 if FAST else 3
    for i in range(n_patterns):
        pat = rng.getrandbits(16)
        await serial_load(dut, pat)
        assert cells(dut) == pat, f"[{i}] load {cells(dut):#06x} != {pat:#06x}"

        exp = conway_next(pat)
        await do_step(dut, speed=0)
        assert cells(dut) == exp, \
            f"[{i}] pat {pat:#06x}: 1 gen -> {cells(dut):#06x}, expected {exp:#06x}"

        exp = pat_after(exp, 2)
        await do_step(dut, speed=1)
        assert cells(dut) == exp, \
            f"[{i}] pat {pat:#06x}: +2 gens -> {cells(dut):#06x}, expected {exp:#06x}"


# ---------------------------------------------------------------------------
# 2. Dead-border / corner cases.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_dead_border_cases(dut):
    """Patterns that exercise the constant-0 ring around the grid."""
    start_clock(dut)
    await reset_dut(dut)

    # Hard-coded expectations so a bug in conway_next can't hide a bug in RTL.
    # All ones: every interior/edge cell is overcrowded; corners have exactly
    # 3 neighbours and survive.
    await serial_load(dut, 0xFFFF)
    await do_step(dut, speed=0)
    assert cells(dut) == 0x9009, \
        f"all-ones -> {cells(dut):#06x}, expected corners only 0x9009"

    cases = [
        ("block top-left (still life)",     0x0033),
        ("block bottom-right (still life)", 0xCC00),
        ("block top-right (still life)",    0x00CC),
        ("block bottom-left (still life)",  0x3300),
        ("vertical blinker on left edge",   0x0111),
        ("horizontal blinker on top edge",  0x0007),
        ("vertical blinker on right edge",  0x0888),
        ("horizontal blinker bottom edge",  0xE000),
        ("single corner cell dies",         0x0001),
        ("L in bottom-right corner",        0xC800),
    ]
    for name, pat in cases:
        await serial_load(dut, pat)
        g = pat
        for gen in range(1, 3):
            g = conway_next(g)
            await do_step(dut, speed=0)
            assert cells(dut) == g, \
                f"{name}: gen {gen} {cells(dut):#06x} != {g:#06x}"
        if "still life" in name:
            assert cells(dut) == pat, f"{name}: still life changed"


# ---------------------------------------------------------------------------
# 3. speed = 3 -> 8 generations per step.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_speed3_eight_generations(dut):
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0010_0110_0100
    await serial_load(dut, pat)

    mon = CommitMonitor(dut)
    await do_step(dut, speed=3)
    mon.stop()
    assert mon.count == 8, f"speed=3 committed {mon.count} generations, expected 8"
    exp = pat_after(pat, 8)
    assert cells(dut) == exp, f"speed3 (8 gens) {cells(dut):#06x} != {exp:#06x}"


# ---------------------------------------------------------------------------
# 4. Serial load reaches every cell.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_serial_load_each_cell(dut):
    """Every one-hot pattern, plus all-ones / all-zeros, loads exactly."""
    start_clock(dut)
    await reset_dut(dut)
    pats = [1 << i for i in range(16)] + [0xFFFF, 0x0000, 0xA5A5, 0x5A5A]
    for pat in pats:
        await serial_load(dut, pat)
        assert cells(dut) == pat, f"loaded {cells(dut):#06x} != {pat:#06x}"


# ---------------------------------------------------------------------------
# 5. Commits only during vertical blanking (no tearing).
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_commits_only_in_vblank(dut):
    start_clock(dut)
    await reset_dut(dut)
    await serial_load(dut, 0b0000_0010_0110_0100)

    mon = CommitMonitor(dut, watch_tearing=True)
    await do_step(dut, speed=2)
    await do_step(dut, speed=1)
    mon.stop()

    assert mon.count == 6, f"expected 6 commits, saw {mon.count}"
    for v in mon.vpos:
        assert V_DISPLAY <= v < V_TOTAL, \
            f"commit at vpos={v}, outside vertical blanking [{V_DISPLAY},{V_TOTAL})"
    assert not mon.tear_events, \
        f"cells changed while display_on was high at (hpos,vpos)={mon.tear_events[:5]}"


# ---------------------------------------------------------------------------
# 6. Step during load is ignored.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_step_ignored_during_load(dut):
    """A step edge while load_en is high must not schedule any generation."""
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0000_0111_0000   # blinker: would visibly change if stepped

    mon = CommitMonitor(dut)
    # Serial load by hand, raising step half-way through and keeping it high.
    await FallingEdge(dut.clk)
    step = 0
    for t in range(16):
        if t == 6:
            step = 1
        bit = (pat >> SNAKE_ORDER[t]) & 1
        dut.ui_in.value = ui(step=step, load_en=1, load_data=bit)
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
    # Leave load mode with step still high (no new rising edge), then drop it.
    dut.ui_in.value = ui(step=1)
    await ClockCycles(dut.clk, 5)
    dut.ui_in.value = ui()
    await RisingEdge(dut.clk)
    assert cells(dut) == pat, f"load {cells(dut):#06x} != {pat:#06x}"

    for _ in range(3 * FRAME):
        await RisingEdge(dut.clk)
        assert int(dut.user_project.u_core.busy.value) == 0, \
            "core went busy from a step edge that happened during load"
    mon.stop()
    assert mon.count == 0, f"{mon.count} generations ran after a step during load"
    assert cells(dut) == pat, "grid changed after a step during load"


# ---------------------------------------------------------------------------
# 7. Step is edge-triggered.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_step_edge_triggered(dut):
    """Holding step high for several frames runs exactly one burst."""
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0000_0111_0000
    await serial_load(dut, pat)

    mon = CommitMonitor(dut)
    dut.ui_in.value = ui(step=0, speed=0)
    await ClockCycles(dut.clk, 5)
    dut.ui_in.value = ui(step=1, speed=0)
    await ClockCycles(dut.clk, 4 * FRAME)
    dut.ui_in.value = ui()
    await RisingEdge(dut.clk)
    mon.stop()

    assert mon.count == 1, f"step held high ran {mon.count} generations, expected 1"
    assert cells(dut) == conway_next(pat)


# ---------------------------------------------------------------------------
# 8. Step during a burst reloads the remaining count.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_step_during_burst_reloads(dut):
    """speed=2 burst, re-stepped after its first commit: 1 + 4 = 5 gens."""
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0010_0110_0100
    await serial_load(dut, pat)

    mon = CommitMonitor(dut)
    dut.ui_in.value = ui(step=0, speed=2)
    await ClockCycles(dut.clk, 5)
    dut.ui_in.value = ui(step=1, speed=2)
    await wait_until(dut, lambda: mon.count >= 1, 2 * FRAME,
                     "first generation of the burst never committed")
    # New rising edge mid-burst.
    dut.ui_in.value = ui(step=0, speed=2)
    await ClockCycles(dut.clk, 5)
    dut.ui_in.value = ui(step=1, speed=2)
    await ClockCycles(dut.clk, 5)
    await wait_not_busy(dut, 6 * FRAME, "reloaded burst never finished")
    dut.ui_in.value = ui()
    await RisingEdge(dut.clk)
    mon.stop()

    assert mon.count == 5, f"re-stepped burst ran {mon.count} generations, expected 5"
    exp = pat_after(pat, 5)
    assert cells(dut) == exp, f"after 5 gens {cells(dut):#06x} != {exp:#06x}"


# ---------------------------------------------------------------------------
# 9. Reset in the middle of a burst.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_reset_mid_burst(dut):
    """Reset cancels a running burst and clears the grid."""
    start_clock(dut)
    await reset_dut(dut)
    await serial_load(dut, 0b0000_0010_0110_0100)

    mon = CommitMonitor(dut)
    dut.ui_in.value = ui(step=0, speed=3)
    await ClockCycles(dut.clk, 5)
    dut.ui_in.value = ui(step=1, speed=3)
    await wait_until(dut, lambda: mon.count >= 2, 4 * FRAME,
                     "burst did not reach its 2nd generation")
    mon.stop()

    await reset_dut(dut)   # also drives step low
    assert int(dut.user_project.u_core.busy.value) == 0, "busy still set after reset"
    assert cells(dut) == 0, f"grid not cleared by reset: {cells(dut):#06x}"

    # The cancelled burst must not resume on a freshly loaded grid.
    pat = 0b0000_0000_0111_0000
    await serial_load(dut, pat)
    mon = CommitMonitor(dut)
    await ClockCycles(dut.clk, 3 * FRAME)
    mon.stop()
    assert mon.count == 0, f"{mon.count} generations ran after reset"
    assert cells(dut) == pat, "grid advanced after reset without a step"


# ---------------------------------------------------------------------------
# 10. Per-cell colour mapping (port-only).
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_display_per_cell(dut):
    """The colour at each cell's centre matches the loaded grid, read
    purely from uo_out (no internal signals)."""
    start_clock(dut)
    await reset_dut(dut)
    rng = random.Random(4242)
    if FAST:
        pats = [0x0000, 0xFFFF, 0xA5A5, 0x5A5A, 0x0001, 0x8000, 0x0008, 0x1000]
        pats += [rng.getrandbits(16) for _ in range(6)]
    else:
        pats = [0xA5A5, 0x0001 | 0x8000]
    for pat in pats:
        await serial_load(dut, pat)
        seen = await cells_from_pixels(dut)
        assert seen == pat, f"display shows {seen:#06x}, loaded {pat:#06x}"


# ---------------------------------------------------------------------------
# 11. Blanking is black.
# ---------------------------------------------------------------------------

@cocotb.test(skip=GL)
async def test_blanking_is_black(dut):
    """No colour bit may be set outside the 640x480 visible area."""
    start_clock(dut)
    await reset_dut(dut)
    await serial_load(dut, 0xFFFF)   # all white: worst case for leaks

    timing = dut.user_project.u_timing
    prev_on = 1
    checked = 0
    saw_visible_colour = False
    for _ in range(FRAME + 2 * H_TOTAL):
        await RisingEdge(dut.clk)
        on = int(timing.display_on.value)
        col = colour(dut)
        # uo_out is registered one clock behind display_on; only judge
        # samples where display_on is low on both sides of that delay.
        if on == 0 and prev_on == 0:
            checked += 1
            assert col == 0, \
                f"colour {col:#04x} during blanking at " \
                f"hpos={int(timing.hpos.value)} vpos={int(timing.vpos.value)}"
        elif on == 1 and prev_on == 1 and col != 0:
            saw_visible_colour = True
        prev_on = on
    assert checked > 0
    assert saw_visible_colour, "never saw colour in the visible area"


# ---------------------------------------------------------------------------
# 12. Sync pulse widths.
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_sync_pulse_widths(dut):
    """hsync is low for H_SYNC clocks; vsync is low for V_SYNC lines."""
    start_clock(dut)
    await reset_dut(dut)

    async def low_width(get, max_cycles):
        # Align to a falling edge, then count clocks until the rising edge.
        prev = get(dut)
        for _ in range(max_cycles):
            await RisingEdge(dut.clk)
            cur = get(dut)
            if prev == 1 and cur == 0:
                break
            prev = cur
        else:
            assert False, "no falling edge seen"
        n = 0
        while get(dut) == 0:
            n += 1
            assert n <= max_cycles, "pulse never ended"
            await RisingEdge(dut.clk)
        return n

    for _ in range(3):
        w = await low_width(hsync, 2 * H_TOTAL)
        assert w == H_SYNC, f"hsync low for {w} clocks, expected {H_SYNC}"
    w = await low_width(vsync, 2 * FRAME)
    assert w == V_SYNC * H_TOTAL, \
        f"vsync low for {w} clocks, expected {V_SYNC * H_TOTAL} ({V_SYNC} lines)"


# ---------------------------------------------------------------------------
# 13. Port-only stepping test (runs unchanged on a gate-level netlist).
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_ports_only_step(dut):
    """Load, step and verify using only top-level pins: no u_core/u_timing
    references, so this also works with GATES=yes."""
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0010_0110_0100
    await serial_load(dut, pat)
    assert await cells_from_pixels(dut) == pat, "loaded grid not displayed"

    g = pat
    # Gate-level sim is far slower than RTL, so only speed 0 there.
    for speed in ((0,) if GL else (0, 1)):
        dut.ui_in.value = ui(step=0, speed=speed)
        await ClockCycles(dut.clk, 5)
        dut.ui_in.value = ui(step=1, speed=speed)
        # Burst finishes within 2^speed frames of the step (+ margin).
        await ClockCycles(dut.clk, (2 ** speed + 1) * FRAME)
        dut.ui_in.value = ui(speed=speed)
        g = pat_after(g, 2 ** speed)
        seen = await cells_from_pixels(dut)
        assert seen == g, f"speed={speed}: display {seen:#06x}, expected {g:#06x}"
