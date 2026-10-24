// Signed divide-by-4 for 8-bit samples, rounding toward zero like C integer division
// (so -5 / 4 = -1 and 5 / 4 = 1).
module div4_trunc (
    input  logic signed [7:0] a,
    output logic signed [7:0] y
);
    assign y = a >>> 2;
endmodule
