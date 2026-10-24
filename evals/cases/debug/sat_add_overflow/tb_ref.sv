`timescale 1ns/1ps
module tb_ref;
    logic signed [7:0] a, b, y;
    logic ovf;
    int errors = 0;
    sat_add8 dut (.*);
    initial begin
        for (int i = -128; i < 128; i++) begin
            for (int j = -128; j < 128; j++) begin
                int sum, expected;
                sum = i + j;
                expected = sum > 127 ? 127 : sum < -128 ? -128 : sum;
                a = 8'(i); b = 8'(j);
                #1;
                if (y !== 8'(expected) || ovf !== (sum != expected)) begin
                    if (errors < 5) $display("ERROR: %0d + %0d: expected y=%0d ovf=%0b got y=%0d ovf=%0b", i, j, expected, sum != expected, y, ovf);
                    errors++;
                end
            end
        end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
