// 4-bit Gray-code counter: bin counts up by one each enabled cycle and wraps; gray is its Gray
// code, so exactly one bit of gray changes per enabled cycle and none change otherwise.
module gray4 (
    input  logic       clk,
    input  logic       rst,
    input  logic       en,
    output logic [3:0] bin,
    output logic [3:0] gray
);
    always_ff @(posedge clk) begin
        if (rst)
            bin <= 4'd0;
        else if (en)
            bin <= bin + 4'd1;
    end

    assign gray = {~bin[3], bin[3:1] ^ bin[2:0]};
endmodule
