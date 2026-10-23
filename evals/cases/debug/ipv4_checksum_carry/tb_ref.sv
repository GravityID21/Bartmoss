`timescale 1ns/1ps
module tb_ref;
    logic clk = 0, rst_n = 0, init = 0, valid = 0, finalize = 0;
    logic [15:0] data_in = 0, checksum;
    logic checksum_ok;
    int errors = 0;
    logic [15:0] hdr [10] = '{16'h4500, 16'h0073, 16'h0000, 16'h4000, 16'h4011,
                              16'h0000, 16'hC0A8, 16'h0001, 16'hC0A8, 16'h00C7};

    ipv4_checksum dut (.*);
    always #5 clk = ~clk;

    task automatic pulse_init();
        @(negedge clk) init = 1;
        @(negedge clk) init = 0;
    endtask
    task automatic word(input logic [15:0] w);
        @(negedge clk) begin valid = 1; data_in = w; end
        @(negedge clk) valid = 0;
    endtask
    task automatic fin();
        @(negedge clk) finalize = 1;
        @(negedge clk) finalize = 0;
    endtask

    initial begin
        repeat (2) @(posedge clk);
        @(negedge clk) rst_n = 1;
        // Wikipedia IPv4 example: checksum field zero -> expect B861
        pulse_init(); foreach (hdr[i]) word(hdr[i]); fin();
        if (checksum !== 16'hB861) begin $display("ERROR: header checksum expected B861 got %h", checksum); errors++; end
        // Same header with the checksum filled in -> valid
        pulse_init(); foreach (hdr[i]) word(i == 5 ? 16'hB861 : hdr[i]); fin();
        if (checksum_ok !== 1'b1) begin $display("ERROR: checksum_ok expected 1 got %b (checksum %h)", checksum_ok, checksum); errors++; end
        // Sum that needs a second end-around carry: FFFF + FFFF + 0001 -> ~0001 = FFFE
        pulse_init(); word(16'hFFFF); word(16'hFFFF); word(16'h0001); fin();
        if (checksum !== 16'hFFFE) begin $display("ERROR: carry case expected FFFE got %h", checksum); errors++; end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
