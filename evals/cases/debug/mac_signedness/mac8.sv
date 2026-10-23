// Signed multiply-accumulate. Each cycle `en` is high, acc <= acc + a * b.
// `clr` synchronously zeroes the accumulator (it takes priority over `en`).
module mac8 (
    input  logic               clk,
    input  logic               rst_n,
    input  logic               clr,
    input  logic               en,
    input  logic signed [7:0]  a,
    input  logic signed [7:0]  b,
    output logic signed [19:0] acc
);
    logic [15:0] product;

    assign product = a * b;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            acc <= '0;
        else if (clr)
            acc <= '0;
        else if (en)
            acc <= acc + product;
    end
endmodule
