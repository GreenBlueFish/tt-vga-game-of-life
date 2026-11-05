/*
 * VGA timing generator for 640x480 @ 60 Hz (25.175 MHz pixel clock)
 * SPDX-License-Identifier: Apache-2.0
 *
 * Generates horizontal/vertical sync pulses and the current beam
 * position (hpos, vpos). `display_on` is high only while the beam is
 * inside the 640x480 visible region, so a pixel generator should only
 * drive colour while `display_on` is asserted (VGA requires the signal
 * to be black during blanking/porch intervals).
 *
 * Timing reference: https://en.wikipedia.org/wiki/Video_Graphics_Array
 *
 * NOTE on sync polarity: 640x480@60 uses *negative* sync (sync pulse is
 * logic 0 during the pulse, logic 1 otherwise). The TinyVGA Pmod and most
 * monitors expect this. The sync outputs below are driven active-low to
 * match: `hsync`/`vsync` are 1 during the visible/porch region and 0
 * during the sync pulse.
 */
`default_nettype none

module vga_timing (
    input  wire       clk,         // 25.175 MHz pixel clock
    input  wire       reset,       // synchronous, active-high
    output wire       hsync,        // active-low horizontal sync
    output wire       vsync,        // active-low vertical sync
    output wire       display_on,   // high inside the 640x480 visible area
    output wire       vblank_start, // 1-cycle pulse at the start of v-blanking
    output reg  [9:0] hpos,         // 0..799 horizontal beam position
    output reg  [9:0] vpos          // 0..524 vertical beam position
);

    // ---- Frame timing ----
    // The real chip uses full 640x480@60 timing. For *simulation only*, define
    // SIM_FAST_FRAME to shrink the frame dramatically so a full frame is a few
    // hundred clocks instead of 420,000 - this makes cocotb / the interactive
    // GUI usable without waiting ~1 second per frame. SIM_FAST_FRAME is never
    // defined for synthesis, so the taped-out design is unaffected.
    //
    // The visible area stays 4x4-divisible (so the grid still maps cleanly):
    // fast mode uses a 40x20 visible region with tiny porches.
`ifdef SIM_FAST_FRAME
    // Horizontal timing (pixels) - shrunk for simulation.
    localparam H_DISPLAY = 40;
    localparam H_FRONT   =  2;
    localparam H_SYNC     =  4;
    localparam H_BACK    =  2;

    // Vertical timing (lines) - shrunk for simulation.
    localparam V_DISPLAY = 20;
    localparam V_FRONT   =  1;
    localparam V_SYNC     =  2;
    localparam V_BACK    =  1;
`else
    // Horizontal timing (pixels) - real 640x480@60.
    localparam H_DISPLAY = 640; // visible width
    localparam H_FRONT   =  16; // front porch
    localparam H_SYNC    =  96; // sync pulse width
    localparam H_BACK    =  48; // back porch

    // Vertical timing (lines) - real 640x480@60.
    localparam V_DISPLAY = 480; // visible height
    localparam V_FRONT   =  10; // front porch
    localparam V_SYNC    =   2; // sync pulse width
    localparam V_BACK    =  33; // back porch
`endif

    // Derived boundaries
    localparam H_SYNC_START = H_DISPLAY + H_FRONT;                     // 656
    localparam H_SYNC_END   = H_DISPLAY + H_FRONT + H_SYNC - 1;        // 751
    localparam H_MAX        = H_DISPLAY + H_FRONT + H_SYNC + H_BACK - 1; // 799

    localparam V_SYNC_START = V_DISPLAY + V_FRONT;                     // 490
    localparam V_SYNC_END   = V_DISPLAY + V_FRONT + V_SYNC - 1;        // 491
    localparam V_MAX        = V_DISPLAY + V_FRONT + V_SYNC + V_BACK - 1; // 524

    wire hmaxed = (hpos == H_MAX) || reset;
    wire vmaxed = (vpos == V_MAX) || reset;

    // Horizontal counter: advances every pixel clock.
    always @(posedge clk) begin
        if (hmaxed)
            hpos <= 10'd0;
        else
            hpos <= hpos + 10'd1;
    end

    // Vertical counter: advances once per completed scanline.
    always @(posedge clk) begin
        if (hmaxed) begin
            if (vmaxed)
                vpos <= 10'd0;
            else
                vpos <= vpos + 10'd1;
        end
    end

    // Visible region.
    assign display_on = (hpos < H_DISPLAY) && (vpos < V_DISPLAY);

    // Active-low sync pulses (negative polarity for 640x480@60).
    assign hsync = ~((hpos >= H_SYNC_START) && (hpos <= H_SYNC_END));
    assign vsync = ~((vpos >= V_SYNC_START) && (vpos <= V_SYNC_END));

    // One-cycle pulse on the very first pixel clock of vertical blanking
    // (the moment the beam leaves the visible area, at vpos == V_DISPLAY,
    // hpos == 0). The golchip core uses this to start a generation so the
    // grid is only updated while the screen is blanked.
    assign vblank_start = (vpos == V_DISPLAY) && (hpos == 10'd0);

endmodule

`default_nettype wire
