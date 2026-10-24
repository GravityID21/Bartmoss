`timescale 1ns/1ps
module tb;
  logic clk = 1'b0;
  logic rst = 1'b1;
  always #5 clk = ~clk;
  logic wr_en = 1'b0, rd_en = 1'b0;
  logic [7:0] wr_data = 8'h00;
  logic [7:0] rd_data;
  logic [2:0] count;
  logic full, empty;
  fifo4 dut (.clk(clk), .rst(rst), .wr_en(wr_en), .wr_data(wr_data), .rd_en(rd_en), .rd_data(rd_data),
             .count(count), .full(full), .empty(empty));
  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, tb);
    #12 rst = 1'b0;
    repeat (6) begin
      @(negedge clk) begin wr_en = 1'b1; wr_data = wr_data + 8'h10; end
    end
    @(negedge clk) wr_en = 1'b0;
    rd_en = 1'b1;
    repeat (6) @(negedge clk);
    rd_en = 1'b0;
    repeat (2) @(negedge clk);
    $finish;
  end
endmodule
