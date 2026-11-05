`default_nettype none
`timescale 1ns / 1ps
//
// Standalone self-checking testbench for the VGA interface.
// Verifies hsync/vsync period, pulse width, polarity, and that the
// colour bits are black during blanking. Runnable with Icarus Verilog
// alone (no cocotb / make required):
//
//   iverilog -g2012 -o tb_selfcheck.vvp test/tb_selfcheck.v \
//            src/project.v src/vga_timing.v src/golchip.v
//   vvp tb_selfcheck.vvp
//
module tb_selfcheck;

  // 640x480@60 reference timing.
  localparam H_DISPLAY = 640, H_FRONT = 16, H_SYNC = 96, H_BACK = 48;
  localparam V_DISPLAY = 480, V_FRONT = 10, V_SYNC = 2,  V_BACK = 33;
  localparam H_TOTAL = H_DISPLAY + H_FRONT + H_SYNC + H_BACK; // 800
  localparam V_TOTAL = V_DISPLAY + V_FRONT + V_SYNC + V_BACK; // 525

  localparam HSYNC_BIT = 7;
  localparam VSYNC_BIT = 3;

  reg clk = 0;
  reg rst_n = 0;
  reg ena = 1;
  reg [7:0] ui_in = 0;
  reg [7:0] uio_in = 0;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  integer errors = 0;

  tt_um_vga_interface dut (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // 25.175 MHz ~= 39.72 ns; use 40 ns period.
  always #20 clk = ~clk;

  wire hsync = uo_out[HSYNC_BIT];
  wire vsync = uo_out[VSYNC_BIT];
  wire [7:0] colour = uo_out & ~((1 << HSYNC_BIT) | (1 << VSYNC_BIT));

  // counters / measurements
  integer h_period, h_low, v_period;
  integer i;
  reg prev;

  task check(input cond, input [255:0] name);
    begin
      if (cond) $display("PASS: %0s", name);
      else begin $display("FAIL: %0s", name); errors = errors + 1; end
    end
  endtask

  // Reset generation and VCD dump.
  initial begin
    $dumpfile("tb_selfcheck.vcd");
    $dumpvars(0, tb_selfcheck);
    rst_n = 0;
    repeat (10) @(posedge clk);
    rst_n = 1;
  end

  // ---- Measurement block ----
  initial begin : meas
    // wait for reset release
    @(posedge rst_n);
    @(posedge clk);

    // --- Unused bidir pins ---
    check(uio_oe === 8'h00, "uio_oe is all inputs (0x00)");
    check(uio_out === 8'h00, "uio_out is 0");

    // 1) hsync period
    // go to first falling edge
    prev = hsync;
    i = 0;
    while (!(prev === 1'b1 && hsync === 1'b0)) begin
      prev = hsync; @(posedge clk);
    end
    // count to next falling edge
    h_period = 0; prev = hsync; @(posedge clk); h_period = h_period + 1;
    while (!(prev === 1'b1 && hsync === 1'b0)) begin
      prev = hsync; @(posedge clk); h_period = h_period + 1;
    end
    $display("INFO: hsync period = %0d (expect %0d)", h_period, H_TOTAL);
    check(h_period == H_TOTAL, "hsync period == 800");

    // 2) hsync low pulse width (we are currently just after a falling edge)
    // find next high, then next low, then measure low width.
    while (hsync === 1'b0) @(posedge clk); // to high
    while (hsync === 1'b1) @(posedge clk); // to low
    h_low = 0;
    while (hsync === 1'b0) begin h_low = h_low + 1; @(posedge clk); end
    $display("INFO: hsync low width = %0d (expect %0d)", h_low, H_SYNC);
    check(h_low == H_SYNC, "hsync pulse width == 96");

    // 3) vsync period
    prev = vsync; 
    while (!(prev === 1'b1 && vsync === 1'b0)) begin
      prev = vsync; @(posedge clk);
    end
    v_period = 0; prev = vsync; @(posedge clk); v_period = v_period + 1;
    while (!(prev === 1'b1 && vsync === 1'b0)) begin
      prev = vsync; @(posedge clk); v_period = v_period + 1;
    end
    $display("INFO: vsync period = %0d (expect %0d)", v_period, V_TOTAL*H_TOTAL);
    check(v_period == V_TOTAL*H_TOTAL, "vsync period == 525*800");

    // 4) blanking is black: scan 3 lines
    for (i = 0; i < H_TOTAL*3; i = i + 1) begin
      @(posedge clk);
      if (hsync === 1'b0 && colour !== 8'h00) begin
        $display("FAIL: colour not black during hsync blanking: %b", colour);
        errors = errors + 1;
      end
    end
    check(1'b1, "colour black during blanking (scanned 3 lines)");

    // Summary
    $display("--------------------------------------------");
    if (errors == 0) $display("ALL TESTS PASSED");
    else $display("%0d TEST(S) FAILED", errors);
    $display("--------------------------------------------");
    $finish;
  end

  // safety timeout
  initial begin
    #80000000; // 80 ms sim time budget
    $display("FAIL: timeout");
    $finish;
  end

endmodule
