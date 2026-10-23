// axis_register_slice.sv -- Full-throughput AXI-Stream skid buffer.
//
// Two-deep storage (main + skid) so the input-side tready is driven
// from a registered flag (not combinatorially from output tready).
// Steady-state 1 beat/cycle throughput regardless of intermittent
// stalls on either side. The point of this module is to break the
// backpressure timing path between two AXIS stages.
//
// State invariants:
//   skid_valid=0, main_valid=0 -- EMPTY (s_ready=1, m_valid=0)
//   skid_valid=0, main_valid=1 -- ONE   (s_ready=1, m_valid=1)
//   skid_valid=1, main_valid=1 -- TWO   (s_ready=0, m_valid=1)
//   skid_valid=1, main_valid=0 -- ILLEGAL (asserted in sim)
//
// Carries the full AXIS beat (tdata + tkeep + tlast + tuser).

module axis_register_slice #(
    parameter int DATA_WIDTH = 8,
    parameter int USER_WIDTH = 1
)(
    axis_if.slave  s_axis,
    axis_if.master m_axis
);

    localparam int KEEP_WIDTH = DATA_WIDTH / 8;

    logic [DATA_WIDTH-1:0] main_data, skid_data;
    logic [KEEP_WIDTH-1:0] main_keep, skid_keep;
    logic [USER_WIDTH-1:0] main_user, skid_user;
    logic                  main_last, skid_last;
    logic                  main_valid, skid_valid;

    logic s_fire, m_fire;
    assign s_fire = s_axis.tvalid && s_axis.tready;
    assign m_fire = m_axis.tvalid && m_axis.tready;

    always_ff @(posedge s_axis.aclk or negedge s_axis.aresetn) begin
        if (!s_axis.aresetn) begin
            main_valid <= 1'b0;
            skid_valid <= 1'b0;
        end else begin
            if (m_fire) begin
                // Output drained -- main updates from skid, then input, else clears.
                if (skid_valid) begin
                    main_data  <= s_axis.tdata;
                    main_keep  <= skid_keep;
                    main_user  <= skid_user;
                    main_last  <= skid_last;
                    main_valid <= 1'b1;
                    skid_valid <= 1'b0;
                end else if (s_fire) begin
                    main_data  <= s_axis.tdata;
                    main_keep  <= s_axis.tkeep;
                    main_user  <= s_axis.tuser;
                    main_last  <= s_axis.tlast;
                    main_valid <= 1'b1;
                end else begin
                    main_valid <= 1'b0;
                end
            end else begin
                // Output stalled -- input goes to main if empty, else to skid.
                if (s_fire) begin
                    if (main_valid) begin
                        skid_data  <= s_axis.tdata;
                        skid_keep  <= s_axis.tkeep;
                        skid_user  <= s_axis.tuser;
                        skid_last  <= s_axis.tlast;
                        skid_valid <= 1'b1;
                    end else begin
                        main_data  <= s_axis.tdata;
                        main_keep  <= s_axis.tkeep;
                        main_user  <= s_axis.tuser;
                        main_last  <= s_axis.tlast;
                        main_valid <= 1'b1;
                    end
                end
            end
        end
    end

    // Input can accept when skid is empty -- registered flag, no
    // combinational dependency on m_axis.tready.
    assign s_axis.tready = !skid_valid;

    assign m_axis.tvalid = main_valid;
    assign m_axis.tdata  = main_data;
    assign m_axis.tkeep  = main_keep;
    assign m_axis.tuser  = main_user;
    assign m_axis.tlast  = main_last;

endmodule
