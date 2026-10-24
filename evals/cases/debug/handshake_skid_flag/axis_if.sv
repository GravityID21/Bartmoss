// axis_if.sv -- Parameterised AXI-Stream SystemVerilog interface.
//
// Mirrors the conventions used by axi4_if.sv in this project: individual
// signal nets (no packed struct), explicit master/slave/monitor modports,
// active-low aresetn. Carries the standard AXI-Stream signal set --
// tvalid, tready, tdata, tkeep (byte enables), tlast, tuser.
//
// Parameters:
//   DATA_WIDTH -- tdata width in bits. Must be a multiple of 8. Default 8.
//   USER_WIDTH -- tuser width in bits. ModelSim doesn't allow 0-width
//                 nets so we default to 1; sinks that don't use tuser
//                 can tie it off.
//   KEEP_WIDTH -- DERIVED from DATA_WIDTH (bytes per beat). NOT a
//                 port-list parameter; users must not override.
//
// Modports:
//   master   -- producer side (drives tvalid/tdata/tkeep/tlast/tuser,
//               receives tready)
//   slave    -- consumer side (mirror)
//   monitor  -- all-input view for passive observation (testbench taps,
//               protocol checkers, perf probes)

interface axis_if #(
    parameter int DATA_WIDTH = 8,
    parameter int USER_WIDTH = 1
)(
    input logic aclk,
    input logic aresetn
);

    localparam int KEEP_WIDTH = DATA_WIDTH / 8;

    logic                    tvalid;
    logic                    tready;
    logic [DATA_WIDTH-1:0]   tdata;
    logic [KEEP_WIDTH-1:0]   tkeep;
    logic                    tlast;
    logic [USER_WIDTH-1:0]   tuser;

    modport master (
        input  aclk, aresetn, tready,
        output tvalid, tdata, tkeep, tlast, tuser
    );

    modport slave (
        input  aclk, aresetn, tvalid, tdata, tkeep, tlast, tuser,
        output tready
    );

    modport monitor (
        input aclk, aresetn,
              tvalid, tready, tdata, tkeep, tlast, tuser
    );

endinterface
