// reset_sync.sv -- Per-domain reset synchronizer.
//
// Async-assert, sync-deassert:
//   - When arst_n_in goes low (asynchronously), srst_n_out drops within
//     one clk cycle (the first stage flop is reset asynchronously).
//   - When arst_n_in releases, srst_n_out stays low for STAGES more clk
//     edges, then deasserts synchronously to clk. This avoids removing
//     reset on a single flop just before another flop in the same domain
//     sees it -- a classic synchronous-design hazard.
//
// One instance per clock domain. The SoC-wide async reset (KEY[0]
// debounced + PHY reset, etc.) feeds the 'arst_n_in' input of every
// instance; each domain's logic uses that domain's 'srst_n_out'.

module reset_sync #(
    parameter int STAGES = 2
)(
    input  logic clk,
    input  logic arst_n_in,
    output logic srst_n_out
);

    (* ASYNC_REG = "TRUE" *) logic [STAGES-1:0] sync_chain;

    always_ff @(posedge clk or negedge arst_n_in) begin
        if (!arst_n_in) sync_chain <= '0;
        else            sync_chain <= {STAGES{1'b1}};
    end

    assign srst_n_out = sync_chain[STAGES-1];

endmodule
