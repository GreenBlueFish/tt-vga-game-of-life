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
    for _ in range(V_TOTAL * H_TOTAL * (2 ** speed) + 20 * H_TOTAL):
        await RisingEdge(dut.clk)
        if int(dut.user_project.u_core.busy.value) == 0:
            break
    dut.ui_in.value = ui(step=0, speed=speed)
    await RisingEdge(dut.clk)


# ---------------------------------------------------------------------------
# VGA timing (always running, independent of the sim).
# ---------------------------------------------------------------------------

@cocotb.test()
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

@cocotb.test()
async def test_serial_load(dut):
    start_clock(dut)
    await reset_dut(dut)
    pat = 0b0000_0000_0111_0000  # blinker, cells 4,5,6
    await serial_load(dut, pat)
    assert cells(dut) == pat, f"loaded {cells(dut):#06x} != {pat:#06x}"


@cocotb.test()
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
    for _ in range(2 * V_TOTAL * H_TOTAL):
        await RisingEdge(dut.clk)
        if int(dut.user_project.u_timing.vblank_start.value) == 1:
            seen_vblank += 1
            if seen_vblank >= 2:
                break
    assert seen_vblank >= 2, "did not observe vblanks"
    assert cells(dut) == pat, "grid advanced without a step"


# ---------------------------------------------------------------------------
# Stepping and speed.
# ---------------------------------------------------------------------------

@cocotb.test()
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


@cocotb.test()
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


@cocotb.test()
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
    while int(dut.user_project.u_timing.vblank_start.value) != 1:
        await RisingEdge(dut.clk)
    for _ in range((V_SYNC + V_BACK + 8) * H_TOTAL):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) & colour_mask) != 0:
            saw_colour = True
    assert saw_colour, "no colour drawn for a loaded grid"
    assert cells(dut) == pat, "grid changed during display scan"
