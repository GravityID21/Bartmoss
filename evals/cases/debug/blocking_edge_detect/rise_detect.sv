// Rising-edge detector: rise is high for one clock after `in` goes from 0 to 1 (both registered).
module rise_detect (
    input  logic clk,
    input  logic rst_n,
    input  logic in,
    output logic rise
);
    logic prev;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prev = 1'b0;
            rise = 1'b0;
        end else begin
            prev = in;
            rise = in & ~prev;
        end
    end
endmodule
