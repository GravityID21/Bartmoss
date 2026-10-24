// Gray-code counter: gray steps through a 3-bit Gray sequence, one bit changing per cycle.
module gray3 (
    input  logic       clk,
    input  logic       rst,
    output logic [2:0] bin,
    output logic [2:0] gray
);
    always_ff @(posedge clk) begin
        if (rst)
            bin <= 3'd0;
        else
            bin <= bin + 3'd1;
    end

    assign gray = bin ^ (bin >> 1);
endmodule
