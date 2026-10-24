`timescale 1ns/1ps
module tb;
  logic clk = 1'b0;
  logic rst = 1'b1;
  always #5 clk = ~clk;
  logic [1:0] req = 2'b00;
  logic [1:0] grant;
  rr_arb2 dut (.clk(clk), .rst(rst), .req(req), .grant(grant));
  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, tb);
    #12 rst = 1'b0;
    @(negedge clk) req = 2'b01;
    @(negedge clk) req = 2'b10;
    @(negedge clk) req = 2'b11;
    repeat (5) @(negedge clk);
    req = 2'b00;
    repeat (2) @(negedge clk);
    $finish;
  end
endmodule
