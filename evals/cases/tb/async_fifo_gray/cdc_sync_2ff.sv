// cdc_sync_2ff.sv -- N-stage flip-flop synchronizer for single-bit (or
// loosely-correlated multi-bit) control signals crossing a clock domain.
//
// Contract:
//   - d_src is launched in the *source* domain (caller's responsibility);
//     this module only owns the receive-side synchronizing flops, all
//     clocked by clk_dst.
//   - WIDTH > 1 is allowed for buses where each bit is independently
//     toggled (e.g. a Gray-coded pointer, where exactly one bit changes
//     per increment); do NOT use this for arbitrary multi-bit buses where
//     two bits may change in the same source cycle -- use cdc_handshake
//     or async_fifo instead.
//   - STAGES defaults to 2; raise to 3+ for very-high-MTBF designs.
//   - INIT is the reset value latched on every stage (also the value
//     present on q_dst until the first STAGES dst-clock edges after a
//     stable d_src.)
//
// Synthesis attributes:
//   - (* ASYNC_REG = "TRUE" *) tells Vivado/Quartus to place the flops
//     close together and exclude them from timing analysis as a stage
//     pair. Quartus also honours `altera_attribute` "PRESERVE"; both are
//     placed here so the module is portable.

module cdc_sync_2ff #(
    parameter int           WIDTH  = 1,
    parameter int           STAGES = 2,
    parameter logic [63:0]  INIT   = 64'd0
)(
    input  logic             clk_dst,
    input  logic             rst_n_dst,
    input  logic [WIDTH-1:0] d_src,
    output logic [WIDTH-1:0] q_dst
);

    (* ASYNC_REG = "TRUE", altera_attribute = "-name SDC_STATEMENT \"set_false_path -to [get_keepers {*|cdc_sync_2ff:*|sync_chain[0]*}]\"" *)
    logic [WIDTH-1:0] sync_chain [STAGES];

    genvar s;
    generate
        for (s = 0; s < STAGES; s++) begin : g_stage
            always_ff @(posedge clk_dst or negedge rst_n_dst) begin
                if (!rst_n_dst) begin
                    sync_chain[s] <= INIT[WIDTH-1:0];
                end else begin
                    sync_chain[s] <= (s == 0) ? d_src : sync_chain[s-1];
                end
            end
        end
    endgenerate

    assign q_dst = sync_chain[STAGES-1];

endmodule
