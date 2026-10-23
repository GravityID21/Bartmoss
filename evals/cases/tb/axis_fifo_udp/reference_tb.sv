// Hidden reference testbench used only to confirm the mutants are detectable. Never shown to the model.
`timescale 1ns/1ps
module tb_axis_fifo;
    localparam int W = 8, D = 16;
    logic clk = 0, rst_n = 0;
    logic wr_valid = 0, wr_last = 0, rd_ready = 0;
    logic [W-1:0] wr_data = 0, rd_data;
    logic wr_ready, rd_valid, rd_last, full, empty;
    logic [$clog2(D):0] fill_level;
    int errors = 0;
    axis_fifo #(.DATA_WIDTH(W), .DEPTH(D)) dut (.*);
    always #5 clk = ~clk;
    initial begin
        #20000 $display("TEST FAILED: timeout"); $finish;
    end
    initial begin
        repeat (2) @(posedge clk);
        @(negedge clk) rst_n = 1;
        // rd_ready held high while empty must not move the read pointer
        @(negedge clk) rd_ready = 1;
        repeat (3) @(posedge clk);
        @(negedge clk) rd_ready = 0;
        for (int i = 0; i < D; i++) begin
            @(negedge clk) begin wr_valid = 1; wr_data = i * 3; wr_last = (i % 4 == 3); end
        end
        @(negedge clk) wr_valid = 0;
        if (!full || wr_ready) begin $display("ERROR: expected full after %0d writes", D); errors++; end
        for (int i = 0; i < D; i++) begin
            #1;
            if (!rd_valid || rd_data !== W'(i * 3) || rd_last !== (i % 4 == 3)) begin
                $display("ERROR: read %0d: expected %0d/%0b got v=%0b %0d/%0b", i, i * 3, i % 4 == 3, rd_valid, rd_data, rd_last);
                errors++;
            end
            @(negedge clk) rd_ready = 1;
            @(negedge clk) rd_ready = 0;
        end
        if (!empty) begin $display("ERROR: not empty after draining"); errors++; end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
