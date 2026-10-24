// Average of two unsigned 8-bit samples, rounded down: y = (a + b) / 2. Must be exact for every input.
module avg8 (
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] y
);
    logic [7:0] sum;

    assign sum = a + b;
    assign y   = sum >> 1;
endmodule
