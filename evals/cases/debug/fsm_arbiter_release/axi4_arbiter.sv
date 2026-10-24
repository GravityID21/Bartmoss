// Parameterized N-input round-robin arbiter with burst-locked grants.
//
// Behavior:
//   - Idle (no grant held): on the next clock edge, if any req is high,
//     grant the requester at the highest-priority position. Priority
//     rotates so the just-served requester is lowest priority next time.
//   - Held: grant is locked one-hot until burst_done pulses. Mid-burst
//     changes in req are ignored -- no preemption.
//   - On burst_done: grant is dropped that same cycle (registered output
//     goes low next clock), the round-robin pointer advances to the
//     just-served requester, and a new arbitration can start the cycle
//     after.
//
// `grant_valid` is just |grant; provided so the caller doesn't have to
// OR-reduce the vector themselves.

module axi4_arbiter #(
  parameter int N = 2
) (
  input  logic         aclk,
  input  logic         aresetn,
  input  logic [N-1:0] req,
  output logic [N-1:0] grant,
  output logic         grant_valid,
  input  logic         burst_done
);

  localparam int IDX_W = (N > 1) ? $clog2(N) : 1;

  logic [N-1:0]      grant_q;
  logic              held_q;
  logic [IDX_W-1:0]  last_idx_q;

  // Combinational next-grant: scan starting one past the last-served idx.
  logic [N-1:0] next_grant;
  always_comb begin
    automatic logic done;
    automatic int   idx;
    done       = 1'b0;
    next_grant = '0;
    for (int i = 0; i < N; i++) begin
      idx = (int'(last_idx_q) + 1 + i) % N;
      if (!done && req[idx]) begin
        next_grant[idx] = 1'b1;
        done            = 1'b1;
      end
    end
  end

  // Encode the one-hot next_grant back to an index for the pointer update.
  logic [IDX_W-1:0] next_idx;
  always_comb begin
    next_idx = '0;
    for (int i = 0; i < N; i++) if (next_grant[i]) next_idx = i[IDX_W-1:0];
  end

  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      grant_q    <= '0;
      held_q     <= 1'b0;
      // Initialize so the first arbitration prefers index 0.
      last_idx_q <= (N > 1) ? (N - 1) : '0;
    end else begin
      if (!held_q) begin
        if (|next_grant) begin
          grant_q    <= next_grant;
          held_q     <= 1'b1;
          last_idx_q <= next_idx;
        end
      end else if (burst_done) begin
        held_q     <= 1'b0;
        // last_idx_q already reflects the requester we just served.
      end
    end
  end

  assign grant       = grant_q;
  assign grant_valid = |grant_q;

endmodule
