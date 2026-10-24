// Priority encoder: idx is the position of the LOWEST set bit of req (bit 0 has the highest priority).
// valid is low and idx is 0 when req is all zeros.
module prienc8 (
    input  logic [7:0] req,
    output logic [2:0] idx,
    output logic       valid
);
    always_comb begin
        idx   = '0;
        valid = 1'b0;
        for (int i = 0; i < 8; i++) begin
            if (req[i]) begin
                idx   = 3'(i);
                valid = 1'b1;
            end
        end
    end
endmodule
