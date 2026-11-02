// Traffic light controller for a main road and a side road. Main is green by default.
// If side_req is high while main is green, and main has been green for at least MIN_GREEN cycles,
// main turns yellow for YELLOW cycles, then side is green for SIDE_GREEN cycles, then side yellow
// for YELLOW cycles, then main is green again (its MIN_GREEN timer restarts).
// Each road's light is one-hot {red, yellow, green}; the road that isn't green or yellow is red.
// Synchronous active-high reset puts main green.
module traffic #(
    parameter int MIN_GREEN = 6,
    parameter int YELLOW = 2,
    parameter int SIDE_GREEN = 4
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       side_req,
    output logic [2:0] main_light,
    output logic [2:0] side_light
);
    typedef enum logic [1:0] {MAIN_G, MAIN_Y, SIDE_G, SIDE_Y} state_t;
    state_t st;
    logic [7:0] t;  // cycles spent in the current state, saturating

    always_ff @(posedge clk) begin
        if (rst) begin
            st <= MAIN_G;
            t <= 8'd0;
        end else begin
            if (t != 8'hFF) t <= t + 8'd1;
            case (st)
                MAIN_G: if (side_req && t >= 8'(MIN_GREEN - 1)) begin st <= MAIN_Y; t <= 8'd0; end
                MAIN_Y: if (t == 8'(YELLOW - 1)) begin st <= SIDE_G; t <= 8'd0; end
                SIDE_G: if (t == 8'(SIDE_GREEN - 1)) begin st <= SIDE_Y; t <= 8'd0; end
                SIDE_Y: if (t == 8'(YELLOW - 1)) begin st <= MAIN_G; t <= 8'd0; end
            endcase
        end
    end

    assign main_light = (st == MAIN_G) ? 3'b001 : (st == MAIN_Y) ? 3'b010 : 3'b100;
    assign side_light = (st == SIDE_G) ? 3'b001 : (st == SIDE_Y) ? 3'b010 : 3'b100;
endmodule
