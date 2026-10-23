// 4-stage serial-in parallel-out shift register. din enters q[0]; data moves one stage per clock.
module shift4 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       din,
    output logic [3:0] q
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            q = 4'b0;
        end else begin
            q[0] = din;
            q[1] = q[0];
            q[2] = q[1];
            q[3] = q[2];
        end
    end
endmodule
