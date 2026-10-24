// Moore sequence detector for the bit pattern 1011 on `din`, with overlap allowed
// (e.g. 1011011 contains two matches). `match` is high for one cycle, in the cycle
// after the final 1 of each occurrence has been sampled.
module seq1011 (
    input  logic clk,
    input  logic rst_n,
    input  logic din,
    output logic match
);
    typedef enum logic [2:0] {IDLE, S1, S10, S101, S1011} state_e;
    state_e state, next;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else        state <= next;
    end

    always_comb begin
        next = state;
        case (state)
            IDLE:  next = din ? S1    : IDLE;
            S1:    next = din ? IDLE  : S10;
            S10:   next = din ? S101  : S10;
            S101:  next = din ? S1011 : S10;
            S1011: next = din ? S1    : S10;
            default: next = IDLE;
        endcase
    end

    assign match = (state == S1011);
endmodule
