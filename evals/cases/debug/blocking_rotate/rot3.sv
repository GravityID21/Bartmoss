// Three-register rotator: on each enabled clock, r0 takes r1, r1 takes r2 and r2 takes r0 (all at once).
// Reset loads 1, 2, 3.
module rot3 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en,
    output logic [7:0] r0,
    output logic [7:0] r1,
    output logic [7:0] r2
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            r0 = 8'd1;
            r1 = 8'd2;
            r2 = 8'd3;
        end else if (en) begin
            r0 = r1;
            r1 = r2;
            r2 = r0;
        end
    end
endmodule
