// Signed 8-bit saturating adder: y = a + b clamped to [-128, 127]; ovf is high when it clamped.
module sat_add8 (
    input  logic signed [7:0] a,
    input  logic signed [7:0] b,
    output logic signed [7:0] y,
    output logic              ovf
);
    logic [8:0] wide;

    assign wide = a + b;
    assign ovf  = wide[8];
    assign y    = ovf ? (a[7] ? 8'sh80 : 8'sh7f) : wide[7:0];
endmodule
