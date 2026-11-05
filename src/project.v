/*
 * Tiny Tapeout VGA Game-of-Life - top module
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wraps the VGA timing generator and the golchip Game-of-Life core, and
 * maps the result onto the standard TinyVGA Pmod pinout (uo_out).
 *
 * TinyVGA Pmod pinout (https://tinytapeout.com/specs/pinouts/):
 *
 *   function | pin        | notes
 *   ---------|------------|-----------------------------------
 *   R1       | uo_out[0]  | red   MSB
 *   G1       | uo_out[1]  | green MSB
 *   B1       | uo_out[2]  | blue  MSB
 *   VSync    | uo_out[3]  | active low
 *   R0       | uo_out[4]  | red   LSB
 *   G0       | uo_out[5]  | green LSB
 *   B0       | uo_out[6]  | blue  LSB
 *   HSync    | uo_out[7]  | active low
 *
 * Control interface on the dedicated inputs (ui_in):
 *
 *   ui_in[7]   : step      - rising edge computes 2^speed generations
 *   ui_in[6]   : run       - reserved (unused; step is the only trigger)
 *   ui_in[5]   : load_en   - serial load enable (shift one cell per clock)
 *   ui_in[4]   : load_data - serial data bit
 *   ui_in[3]   : unused
 *   ui_in[2]   : unused
 *   ui_in[1:0] : speed     - generations per step: 2^speed (1,2,4,8)
 *
 * Writing the grid (serial load):
 *   Hold load_en high and present bits on load_data. Each clock the bit is
 *   shifted one position along the 16-cell snake chain (row 0 left->right,
 *   row 1 right->left, ...). After 16 clocks the whole 4x4 grid is loaded;
 *   the last bit shifted in lands in cell 0.
 *
 * Advancing the simulation:
 *   A rising edge on step runs 2^speed generations. Each generation is
 *   committed during vertical blanking, so the monitor never sees a
 *   half-updated grid. Between steps the grid holds and the VGA keeps
 *   displaying the current (stable) state.
 *
 * Clock: must be driven at 25.175 MHz for a standard 640x480@60 image.
 */
`default_nettype none

module tt_um_vga_interface (
    input  wire [7:0] ui_in,    // Dedicated inputs (control interface)
    output wire [7:0] uo_out,   // Dedicated outputs (TinyVGA Pmod)
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (1 = output)
    input  wire       ena,      // always 1 when powered; ignored
    input  wire       clk,      // 25.175 MHz pixel clock
    input  wire       rst_n     // active-low reset
);

    localparam N = 4;   // 4x4 grid
    localparam W = 2;   // bits for row/col (log2 N)

    // Active-high synchronous reset for the VGA timing block.
    wire reset = ~rst_n;

    // ---- Control decode (serial-load pin map) ----
    wire       step      = ui_in[7];
    wire       run       = ui_in[6];   // reserved / unused
    wire       load_en   = ui_in[5];
    wire       load_data = ui_in[4];
    wire [1:0] speed      = ui_in[1:0];

    // ---- VGA timing ----
    wire       hsync;
    wire       vsync;
    wire       display_on;
    wire       vblank_start;
    wire [9:0] hpos;
    wire [9:0] vpos;

    vga_timing u_timing (
        .clk          (clk),
        .reset        (reset),
        .hsync        (hsync),
        .vsync        (vsync),
        .display_on   (display_on),
        .vblank_start (vblank_start),
        .hpos         (hpos),
        .vpos         (vpos)
    );

    // Visible frame size and derived cell size. These MUST match the timing
    // in vga_timing.v (full 640x480, or the shrunk SIM_FAST_FRAME values used
    // only in simulation). Each of the 4x4 cells is CELL_W x CELL_H pixels.
`ifdef SIM_FAST_FRAME
    localparam HDISP = 40;
    localparam VDISP = 20;
    localparam SEPW  = 1;    // separator width (px)
`else
    localparam HDISP = 640;
    localparam VDISP = 480;
    localparam SEPW  = 4;
`endif
    localparam CELL_W = HDISP / N;   // 160 real / 10 fast
    localparam CELL_H = VDISP / N;   // 120 real / 5  fast

    // Which cell the beam is inside: col = hpos / CELL_W, row = vpos / CELL_H.
    // Divisor is a constant, so this synthesises to fixed logic.
    wire [W-1:0] col = hpos / CELL_W;
    wire [W-1:0] row = vpos / CELL_H;

    // ---- Game-of-Life core ----
    wire               cell_bit;
    wire               gen_tick;
    wire               busy;
    wire [N*N-1:0]     cells;

    golchip_core #(.N(N), .W(W)) u_core (
        .clk          (clk),
        .rst_n        (rst_n),
        .run          (run),
        .load_en      (load_en),
        .load_data    (load_data),
        .step         (step),
        .speed        (speed),
        .clear        (1'b0),          // clear unused: rst_n wipes the grid
        .vblank_start (vblank_start),
        .row          (row),
        .col          (col),
        .cell_bit     (cell_bit),
        .gen_tick     (gen_tick),
        .busy         (busy),
        .cells        (cells)
    );

    // ---- Renderer: cell_bit -> colour, with grid separators ----
    // Thin separators (SEPW px) along the left/top of each cell.
    wire on_vline = (hpos % CELL_W) < SEPW;
    wire on_hline = (vpos % CELL_H) < SEPW;
    wire on_grid_line = on_vline | on_hline;

    reg [1:0] r, g, b;
    always @(*) begin
        if (!display_on) begin
            {r, g, b} = 6'b00_00_00;          // blanking must be black
        end else if (on_grid_line) begin
            {r, g, b} = 6'b01_01_01;          // dim grey separators
        end else if (cell_bit) begin
            {r, g, b} = 6'b11_11_11;          // live cell: white
        end else begin
            {r, g, b} = 6'b00_00_01;          // dead cell: near-black blue
        end
    end

    // Register the pin outputs one stage (recommended for the readmux /
    // clean VGA edges). hsync/vsync are delayed one clock to stay aligned
    // with the registered colour.
    reg [7:0] uo_reg;
    always @(posedge clk) begin
        if (reset)
            uo_reg <= 8'b0;
        else
            uo_reg <= {hsync, b[0], g[0], r[0],
                       vsync, b[1], g[1], r[1]};
    end

    assign uo_out = uo_reg;

    // Bidirectional pins unused.
    assign uio_out = 8'b0;
    assign uio_oe  = 8'b0;

    // Tie off unused signals to silence lint warnings.
    wire _unused = &{ena, uio_in, ui_in[3:2], gen_tick, busy, cells[N*N-1:1], 1'b0};

endmodule

`default_nettype wire
