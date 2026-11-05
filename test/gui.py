# SPDX-License-Identifier: Apache-2.0
#
# Interactive GUI for driving project.v (tt_um_vga_interface) through cocotb.
#
# It opens a Tkinter window with:
#   * physical-style toggle switches for all 8 ui_in pins + rst_n/ena,
#   * helper buttons for the common serial-load / step actions,
#   * a live view of what the VGA would display (decoded from the core grid),
#   * an on-demand "Capture real VGA frame" that scans uo_out pixel-by-pixel
#     through a full 640x480 frame to prove the actual pin output,
#   * a readout of the raw uo_out byte, decoded RGB/sync, busy, and the grid.
#
# HOW IT WORKS (cocotb + Tkinter in one process):
#   cocotb owns the simulator loop, so we cannot let Tkinter block. Instead the
#   cocotb coroutine runs forever: advance the sim a chunk of clocks, apply the
#   current switch values to dut.ui_in, pump the Tk event loop once, repeat.
#   Switch toggles set Python variables that are read on the next chunk.
#
# RUN IT (from the test/ directory, same toolchain as the suite):
#   make -B MODULE=gui
# On Windows run under MSYS2/Git-Bash from a path without spaces (same as the
# test suite). A Tk-capable Python is required (standard on Windows installers).

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Timer

try:
    import tkinter as tk
except Exception as exc:  # pragma: no cover - environment-dependent
    raise RuntimeError(
        "tkinter is not available in this Python. Install a Tk-capable Python "
        "to use the GUI."
    ) from exc

import os

# ---- VGA / grid geometry (must match project.v / vga_timing.v) ----
# When the RTL is built with -DSIM_FAST_FRAME (see run_gui.ps1), the frame is
# shrunk to 40x20 so the sim runs fast. Set GUI_FAST_FRAME=1 so the Python side
# uses the same geometry. Otherwise the full 640x480 timing is assumed.
N = 4                      # 4x4 grid
if os.environ.get("GUI_FAST_FRAME", "0") == "1":
    H_DISPLAY = 40
    V_DISPLAY = 20
    SEP = 1
else:
    H_DISPLAY = 640
    V_DISPLAY = 480
    SEP = 4
CELL_W = H_DISPLAY // N
CELL_H = V_DISPLAY // N

# ui_in bit positions (serial-load pin map in project.v).
BIT_SPEED0  = 0
BIT_SPEED1  = 1
BIT_UNUSED2 = 2
BIT_UNUSED3 = 3
BIT_LOADDAT = 4
BIT_LOADEN  = 5
BIT_RUN     = 6
BIT_STEP    = 7

# Snake stream order: slot t -> cell index (verified in test.py).
SNAKE = [12, 13, 14, 15, 11, 10, 9, 8, 4, 5, 6, 7, 3, 2, 1, 0]

# uo_out bit positions (TinyVGA Pmod).
HSYNC_BIT = 7
VSYNC_BIT = 3


# 2-bit channel pairs -> 0..255 for the preview canvas.
def _chan(two_bit):
    # 2-bit value 0..3 scaled to 0..255.
    return (two_bit * 255) // 3


def uo_to_rgb(uo):
    """Decode a uo_out byte into (r, g, b) 0..255 and (hsync, vsync)."""
    r = ((uo >> 0) & 1) << 1 | ((uo >> 4) & 1)   # R1,R0
    g = ((uo >> 1) & 1) << 1 | ((uo >> 5) & 1)   # G1,G0
    b = ((uo >> 2) & 1) << 1 | ((uo >> 6) & 1)   # B1,B0
    hs = (uo >> HSYNC_BIT) & 1
    vs = (uo >> VSYNC_BIT) & 1
    return (_chan(r), _chan(g), _chan(b), hs, vs)


class GuiState:
    """Shared state between the Tk callbacks and the cocotb coroutine."""

    def __init__(self):
        self.ui_bits = [0] * 8     # ui_in[0..7]
        self.rst_n = 1
        self.ena = 1
        self.running = True        # False -> coroutine exits, window closed
        self.capture_request = False
        self.captured = None       # list of (r,g,b) rows x cols after a scan
        self.status = "idle"


def build_window(state):
    root = tk.Tk()
    root.title("tt_um_vga_interface - interactive VGA / Game of Life")

    # ---------- left: switches ----------
    left = tk.Frame(root, padx=10, pady=10)
    left.grid(row=0, column=0, sticky="n")

    tk.Label(left, text="ui_in switches", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")

    labels = {
        BIT_STEP:    "ui_in[7]  step",
        BIT_RUN:     "ui_in[6]  run (unused)",
        BIT_LOADEN:  "ui_in[5]  load_en",
        BIT_LOADDAT: "ui_in[4]  load_data",
        BIT_UNUSED3: "ui_in[3]  (unused)",
        BIT_UNUSED2: "ui_in[2]  (unused)",
        BIT_SPEED1:  "ui_in[1]  speed[1]",
        BIT_SPEED0:  "ui_in[0]  speed[0]",
    }
    switch_vars = {}
    for bit in (BIT_STEP, BIT_RUN, BIT_LOADEN, BIT_LOADDAT,
                BIT_UNUSED3, BIT_UNUSED2, BIT_SPEED1, BIT_SPEED0):
        var = tk.IntVar(value=0)

        def make_cb(b, v):
            def cb():
                state.ui_bits[b] = v.get()
            return cb

        cb = tk.Checkbutton(left, text=labels[bit], variable=var,
                            onvalue=1, offvalue=0, command=make_cb(bit, var),
                            anchor="w", width=22)
        cb.pack(anchor="w")
        switch_vars[bit] = var

    tk.Label(left, text="").pack()
    # reset / ena
    rst_var = tk.IntVar(value=1)

    def rst_cb():
        state.rst_n = rst_var.get()
    tk.Checkbutton(left, text="rst_n (1 = run, 0 = reset)", variable=rst_var,
                   command=rst_cb, anchor="w", width=22).pack(anchor="w")

    # ---------- helper actions ----------
    tk.Label(left, text="").pack()
    tk.Label(left, text="helpers", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")

    load_entry = tk.Entry(left, width=24)
    load_entry.insert(0, "0000000001110000")
    load_entry.pack(anchor="w")
    tk.Label(left, text="16-bit pattern (cell0 is rightmost)",
             font=("TkDefaultFont", 8)).pack(anchor="w")

    def request_load():
        txt = load_entry.get().strip()
        try:
            val = int(txt, 2) if set(txt) <= {"0", "1"} else int(txt, 0)
        except ValueError:
            state.status = "bad pattern"
            return
        state.load_pattern = val & 0xFFFF
    tk.Button(left, text="Serial-load pattern", command=request_load).pack(anchor="w", pady=2)

    def request_step():
        state.step_request = True
    tk.Button(left, text="Pulse step (2^speed gens)", command=request_step).pack(anchor="w", pady=2)

    def request_capture():
        state.capture_request = True
    tk.Button(left, text="Capture real VGA frame (slow)",
              command=request_capture).pack(anchor="w", pady=2)

    # ---------- right: preview + readout ----------
    right = tk.Frame(root, padx=10, pady=10)
    right.grid(row=0, column=1, sticky="n")

    tk.Label(right, text="VGA preview (decoded from grid)",
             font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
    # Scale the 640x480 frame down for the window.
    SCALE = 0.5
    cw, chh = int(H_DISPLAY * SCALE), int(V_DISPLAY * SCALE)
    canvas = tk.Canvas(right, width=cw, height=chh, bg="black",
                       highlightthickness=1, highlightbackground="#444")
    canvas.pack()

    readout = tk.Label(right, text="", justify="left", font=("TkFixedFont", 9),
                       anchor="w")
    readout.pack(anchor="w", pady=6)

    cap_label = tk.Label(right, text="", justify="left",
                         font=("TkFixedFont", 8), anchor="w")
    cap_label.pack(anchor="w")

    def on_close():
        state.running = False
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    return {
        "root": root, "canvas": canvas, "readout": readout,
        "cap_label": cap_label, "scale": SCALE, "cw": cw, "chh": chh,
    }


def draw_grid(widgets, grid16, state):
    """Draw the 4x4 grid the way project.v colours it."""
    canvas = widgets["canvas"]
    s = widgets["scale"]
    canvas.delete("all")
    sep = max(1, int(SEP * s))
    for r in range(N):
        for c in range(N):
            alive = (grid16 >> (r * N + c)) & 1
            x0 = int(c * CELL_W * s)
            y0 = int(r * CELL_H * s)
            x1 = int((c + 1) * CELL_W * s)
            y1 = int((r + 1) * CELL_H * s)
            # dead = near-black blue (#000055-ish), live = white
            fill = "#ffffff" if alive else "#000040"
            canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
    # grey separators along the top/left of each cell (matches project.v)
    for c in range(N):
        x = int(c * CELL_W * s)
        canvas.create_rectangle(x, 0, x + sep, widgets["chh"], fill="#404040", outline="")
    for r in range(N):
        y = int(r * CELL_H * s)
        canvas.create_rectangle(0, y, widgets["cw"], y + sep, fill="#404040", outline="")


def draw_captured(widgets, frame):
    """Draw a pixel-scanned frame: frame is rows of (r,g,b) sampled on a grid."""
    canvas = widgets["canvas"]
    canvas.delete("all")
    rows = len(frame)
    cols = len(frame[0]) if rows else 0
    if not rows or not cols:
        return
    cw, chh = widgets["cw"], widgets["chh"]
    bw = cw / cols
    bh = chh / rows
    for ry in range(rows):
        for rx in range(cols):
            r, g, b = frame[ry][rx]
            color = f"#{r:02x}{g:02x}{b:02x}"
            canvas.create_rectangle(int(rx * bw), int(ry * bh),
                                    int((rx + 1) * bw), int((ry + 1) * bh),
                                    fill=color, outline="")


def ui_value(state):
    v = 0
    for i in range(8):
        v |= (state.ui_bits[i] & 1) << i
    return v


async def serial_load(dut, state, pattern):
    """Drive a serial load of `pattern` (bit i -> cell i), overriding switches."""
    state.status = "loading..."
    # load_en high, shift 16 bits in snake order
    for t in range(16):
        bit = (pattern >> SNAKE[t]) & 1
        val = (1 << BIT_LOADEN) | (bit << BIT_LOADDAT)
        dut.ui_in.value = val
        await RisingEdge(dut.clk)
    dut.ui_in.value = 0
    await RisingEdge(dut.clk)
    # reflect into the switches so the GUI stays consistent
    for i in range(8):
        state.ui_bits[i] = 0
    state.status = "loaded"


async def pulse_step(dut, state):
    """Rising edge on step; wait for the burst to finish (busy clears)."""
    state.status = "stepping..."
    base = ui_value(state) & ~(1 << BIT_STEP)
    dut.ui_in.value = base
    await RisingEdge(dut.clk)
    dut.ui_in.value = base | (1 << BIT_STEP)
    # wait for busy, bounded so we never hang the GUI
    for _ in range(4_000_000):
        await RisingEdge(dut.clk)
        if int(dut.user_project.u_core.busy.value) == 1:
            break
    for _ in range(40_000_000):
        await RisingEdge(dut.clk)
        if int(dut.user_project.u_core.busy.value) == 0:
            break
    dut.ui_in.value = base
    state.ui_bits[BIT_STEP] = 0
    state.status = "step done"


async def capture_frame(dut, state):
    """Scan one real 640x480 frame, sampling uo_out on a coarse grid of
    points so we can build a small image of the actual pin output."""
    state.status = "capturing real frame..."
    # Sample a grid of points across the visible area. In fast-frame mode the
    # whole frame is tiny (40x20), so sample every pixel; otherwise subsample.
    SX = min(64, H_DISPLAY)
    SY = min(48, V_DISPLAY)
    step_x = max(1, H_DISPLAY // SX)
    step_y = max(1, V_DISPLAY // SY)
    SX = H_DISPLAY // step_x
    SY = V_DISPLAY // step_y
    # Wait for the start of a frame (vblank pulse then first visible line).
    while int(dut.user_project.u_timing.vblank_start.value) != 1:
        await RisingEdge(dut.clk)
    # advance to vpos 0 again (next frame top): wait one full wrap to 0
    # Simpler: sample by reading hpos/vpos and uo at each clock.
    frame = [[(0, 0, 0)] * SX for _ in range(SY)]
    got = 0
    target = SX * SY
    # Scan until we have filled all sample points or a frame's worth of clocks.
    for _ in range(H_DISPLAY * V_DISPLAY + H_DISPLAY * 60):
        await RisingEdge(dut.clk)
        hp = int(dut.user_project.u_timing.hpos.value)
        vp = int(dut.user_project.u_timing.vpos.value)
        if hp < H_DISPLAY and vp < V_DISPLAY:
            if (hp % step_x) == 0 and (vp % step_y) == 0:
                sx = hp // step_x
                sy = vp // step_y
                if 0 <= sx < SX and 0 <= sy < SY:
                    r, g, b, _, _ = uo_to_rgb(int(dut.uo_out.value))
                    if frame[sy][sx] == (0, 0, 0):
                        got += 1
                    frame[sy][sx] = (r, g, b)
        if got >= target:
            break
    state.captured = frame
    state.status = "frame captured"


@cocotb.test()
async def interactive(dut):
    """Open the GUI and drive the DUT from it until the window is closed."""
    import os
    dut._log.info("Launching interactive GUI")
    cocotb.start_soon(Clock(dut.clk, 40, units="ns").start())

    state = GuiState()
    state.load_pattern = None
    state.step_request = False
    widgets = build_window(state)

    # Optional smoke test: GUI_SELFTEST=<N> auto-loads a blinker, pulses one
    # step, and closes after N refresh iterations - lets CI verify the plumbing
    # without a human. Leave it unset (0) for normal interactive use.
    selftest = int(os.environ.get("GUI_SELFTEST", "0"))
    iters = 0

    # initial reset
    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.ui_in.value = 0
    dut.rst_n.value = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1

    CHUNK = 200   # sim clocks between GUI refreshes

    if selftest:
        # Kick off a scripted sequence the self-test will drive.
        state.load_pattern = 0b0000_0000_0111_0000

    try:
        while state.running:
            # Self-test driver: after loading, pulse one step, then quit.
            if selftest:
                iters += 1
                if iters == 3 and getattr(state, "load_pattern", None) is None:
                    state.step_request = True
                if iters > selftest:
                    state.running = False

            # Handle one-shot requests from the GUI.
            if getattr(state, "load_pattern", None) is not None:
                pat = state.load_pattern
                state.load_pattern = None
                await serial_load(dut, state, pat)
            elif getattr(state, "step_request", False):
                state.step_request = False
                await pulse_step(dut, state)
            elif state.capture_request:
                state.capture_request = False
                await capture_frame(dut, state)
            else:
                # Normal interactive stepping: apply switches, run a chunk.
                dut.ena.value = state.ena
                dut.rst_n.value = state.rst_n
                dut.ui_in.value = ui_value(state)
                for _ in range(CHUNK):
                    await RisingEdge(dut.clk)

            # Read state for the display.
            await ReadOnly()
            uo = int(dut.uo_out.value)
            try:
                grid = int(dut.user_project.u_core.cells.value)
            except Exception:
                grid = 0
            try:
                busy = int(dut.user_project.u_core.busy.value)
            except Exception:
                busy = 0
            r, g, b, hs, vs = uo_to_rgb(uo)

            # Update the canvas.
            if state.captured is not None:
                draw_captured(widgets, state.captured)
                state.captured = None
            else:
                draw_grid(widgets, grid, state)

            widgets["readout"].config(text=(
                f"uo_out   = 0x{uo:02X}  ({uo:08b})\n"
                f"RGB      = ({r:3d},{g:3d},{b:3d})   hsync={hs} vsync={vs}\n"
                f"grid     = {grid:016b}\n"
                f"busy     = {busy}    status: {state.status}\n"
                f"ui_in    = 0x{ui_value(state):02X}"
            ))

            # Pump Tk once (non-blocking).
            try:
                widgets["root"].update_idletasks()
                widgets["root"].update()
            except tk.TclError:
                state.running = False
                break

            # Small real-time delay so the sim doesn't spin the CPU flat out.
            await Timer(1, units="ms")
    finally:
        dut._log.info("GUI closed; ending interactive test")
