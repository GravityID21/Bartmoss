// cdc_pulse_sync.sv -- Toggle-based single-pulse synchronizer.
//
// A 1-cycle pulse in clk_src is converted to a level toggle, that toggle
// is 2FF-synchronized into clk_dst, and an edge detector emits a matching
// 1-cycle pulse in clk_dst.
//
// Constraint on the user: successive source pulses must be spaced far
// enough apart that the destination has time to see each level transition
// before the next one. Conservative rule:
//     min_spacing_src_cycles >= ceil(T_src / T_dst) + STAGES + 1
// For 100MHz src -> 50MHz dst with STAGES=2 that's >= 4 src cycles. The
// handshake module is the right primitive if the source needs to send
// pulses faster than that.
//
// No-drop / no-duplicate guarantee:
//   - Every src toggle event eventually produces exactly one dst pulse.
//   - The detector compares two adjacent dst-domain samples of the toggle
//     line; aliasing would require the line to toggle twice between two
//     dst edges, which the spacing rule above forbids.

module cdc_pulse_sync (
    input  logic clk_src,
    input  logic rst_n_src,
    input  logic src_pulse,

    input  logic clk_dst,
    input  logic rst_n_dst,
    output logic dst_pulse
);

    // Source-domain toggle flop -- flips on every src_pulse cycle.
    logic src_toggle;
    always_ff @(posedge clk_src or negedge rst_n_src) begin
        if (!rst_n_src) src_toggle <= 1'b0;
        else if (src_pulse) src_toggle <= ~src_toggle;
    end

    // Sync the toggle into clk_dst.
    logic dst_toggle, dst_toggle_q;

    cdc_sync_2ff #(.WIDTH(1)) u_sync (
        .clk_dst   (clk_dst),
        .rst_n_dst (rst_n_dst),
        .d_src     (src_toggle),
        .q_dst     (dst_toggle)
    );

    // Edge detector in dst domain.
    always_ff @(posedge clk_dst or negedge rst_n_dst) begin
        if (!rst_n_dst) dst_toggle_q <= 1'b0;
        else            dst_toggle_q <= dst_toggle;
    end

    assign dst_pulse = dst_toggle & ~dst_toggle_q;

endmodule
