// Debouncer: out changes to the value of in only after in has differed from out for CYCLES
// consecutive clock cycles; any cycle where in equals out restarts the count. Reset clears out to 0.
module debounce #(
    parameter int CYCLES = 4
) (
    input  logic clk,
    input  logic rst,
    input  logic in,
    output logic out
);
    logic [2:0] count;

    always_ff @(posedge clk) begin
        if (rst) begin
            out <= 1'b0;
            count <= 3'd0;
        end else if (in == out) begin
            count <= 3'd0;
        end else if (count == CYCLES - 1) begin
            out <= in;
            count <= 3'd0;
        end else begin
            count <= count + 3'd1;
        end
    end
endmodule
