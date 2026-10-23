// Modulo-N counter: counts 0..N-1 while enabled, then wraps to 0.
// `wrap` is high during the cycle in which the next enabled edge wraps the count.
module mod_counter #(
    parameter int N = 10
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 en,
    output logic [$clog2(N)-1:0] count,
    output logic                 wrap
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= '0;
        end else if (en) begin
            if (count == N)
                count <= '0;
            else
                count <= count + 1'b1;
        end
    end

    assign wrap = en && (count == N - 1);
endmodule
