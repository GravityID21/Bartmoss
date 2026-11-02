// AXI4-Lite slave with four 32-bit registers at byte offsets 0x0, 0x4, 0x8 and 0xC.
// Registers 0-2 are read/write and reset to 0; register 3 is a read-only ID that always reads
// 32'hB0BA_CAFE. A write to register 3 changes nothing and returns SLVERR (2'b10); every other
// response is OKAY. wstrb selects which bytes of a write are updated.
// The write address and write data channels are independent: either may arrive first, and the
// write is done once both have been accepted. A new write isn't accepted while a write response is
// waiting. Reads take one cycle; rdata/rresp hold until rready.
module axil_regs (
    input  logic        clk,
    input  logic        rst,
    input  logic [3:0]  awaddr,
    input  logic        awvalid,
    output logic        awready,
    input  logic [31:0] wdata,
    input  logic [3:0]  wstrb,
    input  logic        wvalid,
    output logic        wready,
    output logic [1:0]  bresp,
    output logic        bvalid,
    input  logic        bready,
    input  logic [3:0]  araddr,
    input  logic        arvalid,
    output logic        arready,
    output logic [31:0] rdata,
    output logic [1:0]  rresp,
    output logic        rvalid,
    input  logic        rready
);
    logic [31:0] regs [4];
    logic        aw_held, w_held;
    logic [3:0]  aw_q;
    logic [31:0] w_q;
    logic [3:0]  strb_q;

    assign awready = !aw_held && !bvalid;
    assign wready = !w_held && !bvalid;
    assign arready = !rvalid;

    always_ff @(posedge clk) begin
        if (rst) begin
            aw_held <= 1'b0;
            w_held <= 1'b0;
            bvalid <= 1'b0;
            rvalid <= 1'b0;
            for (int i = 0; i < 4; i++) regs[i] <= 32'd0;
        end else begin
            if (awvalid && awready) begin
                aw_held <= 1'b1;
                aw_q <= awaddr;
            end
            if (wvalid && wready) begin
                w_held <= 1'b1;
                w_q <= wdata;
                strb_q <= wstrb;
            end
            if (aw_held && w_held) begin
                aw_held <= 1'b0;
                w_held <= 1'b0;
                bvalid <= 1'b1;
                if (aw_q[3:2] == 2'd3) bresp <= 2'b10;
                else begin
                    bresp <= 2'b00;
                    for (int i = 0; i < 4; i++) if (strb_q[i]) regs[aw_q[3:2]][8 * i +: 8] <= w_q[8 * i +: 8];
                end
            end
            if (bvalid && bready) bvalid <= 1'b0;
            if (arvalid && arready) begin
                rvalid <= 1'b1;
                rresp <= 2'b00;
                rdata <= (araddr[3:2] == 2'd3) ? 32'hB0BA_CAFE : regs[araddr[3:2]];
            end else if (rvalid && rready) rvalid <= 1'b0;
        end
    end
endmodule
