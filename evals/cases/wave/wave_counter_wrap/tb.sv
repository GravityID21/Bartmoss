`timescale 1ns/1ps
module tb;
  logic clk = 1'b0;
  logic rst = 1'b1;
  always #5 clk = ~clk;
  logic en = 1'b0;
  logic [3:0] count;
  logic carry;
  decade_counter dut (.clk(clk), .rst(rst), .en(en), .count(count), .carry(carry));
  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, tb);
    #12 rst = 1'b0;
    en = 1'b1;
    #200 $finish;
  end
endmodule
