`timescale 1ns/1ps
module tb_ref;
    logic [7:0] req;
    logic [2:0] idx;
    logic valid;
    int errors = 0;
    prienc8 dut (.*);
    initial begin
        for (int r = 0; r < 256; r++) begin
            int expected;
            expected = 0;
            for (int i = 7; i >= 0; i--) if (r[i]) expected = i;
            req = 8'(r);
            #1;
            if (valid !== (r != 0) || idx !== 3'(expected)) begin
                if (errors < 5) $display("ERROR: req %b: expected idx %0d valid %0b got idx %0d valid %0b", req, expected, r != 0, idx, valid);
                errors++;
            end
        end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
