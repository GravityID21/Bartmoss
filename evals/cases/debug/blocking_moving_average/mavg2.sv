// Two-tap moving average: each clock, y becomes (x + the previous clock's x) / 2, rounded down.
module mavg2 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [7:0] x,
    output logic [7:0] y
);
    logic [7:0] prev;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prev = '0;
            y    = '0;
        end else begin
            prev = x;
            y    = 8'((9'(x) + 9'(prev)) >> 1);
        end
    end
endmodule
