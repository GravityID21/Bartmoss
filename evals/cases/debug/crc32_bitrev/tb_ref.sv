`timescale 1ns/1ps
module tb_ref;
    logic clk = 0, rst_n = 0, init = 0, valid = 0;
    logic [7:0] data_in = 0;
    logic [31:0] crc_out;
    logic crc_match;
    int errors = 0;
    byte msg [9] = '{"1", "2", "3", "4", "5", "6", "7", "8", "9"};

    crc32_eth dut (.*);
    always #5 clk = ~clk;

    task automatic send(input logic [7:0] b);
        @(negedge clk) begin valid = 1; data_in = b; end
        @(posedge clk); #1 valid = 0;
    endtask

    initial begin
        repeat (2) @(posedge clk);
        @(negedge clk) rst_n = 1;
        foreach (msg[i]) send(msg[i]);
        if (crc_out !== 32'hCBF43926) begin $display("ERROR: crc(\"123456789\") expected CBF43926 got %h", crc_out); errors++; end
        // Append the FCS (LSB first) and check the residue.
        send(8'h26); send(8'h39); send(8'hF4); send(8'hCB);
        if (crc_match !== 1'b1) begin $display("ERROR: crc_match not set after valid FCS"); errors++; end
        @(negedge clk) init = 1;
        @(negedge clk) init = 0;
        send(8'h00);
        if (crc_out !== 32'hD202EF8D) begin $display("ERROR: crc(00) expected D202EF8D got %h", crc_out); errors++; end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
