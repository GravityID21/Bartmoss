`timescale 1ns/1ps
module tb_ref;
    localparam int W = 8, AW = 3, DEPTH = 1 << AW;
    logic wr_clk = 0, rd_clk = 0, wr_rst_n = 0, rd_rst_n = 0, wr_en = 0, rd_en = 0, full, empty;
    logic [W-1:0] wr_data = 0, rd_data;
    int errors = 0, wcount = 0, rcount = 0;
    async_fifo #(.DATA_WIDTH(W), .ADDR_WIDTH(AW)) dut (.*);
    always #5 wr_clk = ~wr_clk;
    always #7 rd_clk = ~rd_clk;
    initial begin #200000 $display("TEST FAILED: timeout"); $finish; end

    task automatic write_until_full();
        forever begin
            @(negedge wr_clk);
            if (full) begin wr_en = 0; return; end
            wr_en = 1; wr_data = W'(wcount * 5 + 1);
            @(posedge wr_clk); wcount++;
            #1 wr_en = 0;
        end
    endtask
    task automatic read_until_empty();
        repeat (4) @(posedge rd_clk);   // let the write pointer sync across
        forever begin
            @(negedge rd_clk);
            if (empty) return;
            if (rd_data !== W'(rcount * 5 + 1)) begin
                $display("ERROR: read %0d expected %h got %h", rcount, W'(rcount * 5 + 1), rd_data); errors++;
            end
            rd_en = 1;
            @(posedge rd_clk); rcount++;
            #1 rd_en = 0;
        end
    endtask

    initial begin
        repeat (3) @(posedge rd_clk);
        wr_rst_n = 1; rd_rst_n = 1;
        repeat (3) @(posedge wr_clk);
        for (int round = 0; round < 3; round++) begin
            automatic int start = wcount;
            write_until_full();
            if (wcount - start != DEPTH) begin
                $display("ERROR: round %0d: accepted %0d writes before full, expected %0d", round, wcount - start, DEPTH);
                errors++;
            end
            read_until_empty();
            if (rcount != wcount) begin $display("ERROR: round %0d: read %0d of %0d", round, rcount, wcount); errors++; end
            repeat (4) @(posedge wr_clk);
        end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
