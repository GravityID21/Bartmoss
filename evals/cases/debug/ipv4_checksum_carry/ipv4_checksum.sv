// ipv4_checksum.sv -- One's complement checksum calculator
// Accumulates 16-bit words, folds carry, inverts for final checksum.
// Used for IPv4 header checksum verification (result should be 0x0000 if valid).
//
// Note: the ipv4_rx module computes checksum inline; this module is provided
// as a reusable standalone alternative.

module ipv4_checksum (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        init,          // reset accumulator
    input  logic        valid,         // input word valid
    input  logic [15:0] data_in,       // 16-bit word to accumulate
    input  logic        finalize,      // latch final checksum
    output logic [15:0] checksum,      // final checksum (valid after finalize)
    output logic        checksum_ok    // 1 if header checksum is valid
);

    logic [31:0] sum;

    // Combinational carry folding (available every cycle)
    logic [16:0] fold1;
    logic [15:0] fold2;
    logic [15:0] cksum_comb;

    always_comb begin
        fold1     = sum[15:0] + sum[31:16];
        fold2     = fold1[15:0];
        cksum_comb = ~fold2;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sum      <= '0;
            checksum <= '0;
        end else if (init) begin
            sum      <= '0;
            checksum <= '0;
        end else if (valid) begin
            sum <= sum + {16'd0, data_in};
        end else if (finalize) begin
            checksum <= cksum_comb;
        end
    end

    // Checksum is valid when result is 0x0000
    // (sum of all header words including checksum field == 0xFFFF → ~0xFFFF == 0x0000)
    assign checksum_ok = (checksum == 16'h0000);

endmodule
