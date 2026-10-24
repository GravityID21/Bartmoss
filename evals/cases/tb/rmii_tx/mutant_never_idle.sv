// rmii_tx.sv -- RMII TX bridge (byte stream → LAN8720 PHY)
//
// Converts byte-serial stream to RMII 2-bit interface (50 MHz, 100 Mbps).
// Serializes each byte as 4 dibits, LSB-first.
//
// Backpressure: tx_ready asserts when ready for a new byte (idle or on
// the last dibit of the current byte).
//
// Output registration: txd and tx_en are registered for clean timing.
//
// Latency: 4 cycles per byte (dibit serialization) @ 50 MHz

module rmii_tx (
    input  logic        rmii_clk,     // 50 MHz REF_CLK
    input  logic        rst_n,

    // Byte-stream input (from async FIFO or MAC)
    input  logic [7:0]  tx_data,
    input  logic        tx_valid,
    output logic        tx_ready,

    // RMII PHY pins
    output logic [1:0]  rmii_txd,
    output logic        rmii_tx_en
);

    // ─── Dibit serializer ──────────────────────────────────────
    logic [7:0] shift_reg;
    logic [1:0] dibit_cnt;
    logic       active;        // currently serializing a byte

    assign tx_ready = !active || (dibit_cnt == 2'd3);

    always_ff @(posedge rmii_clk or negedge rst_n) begin
        if (!rst_n) begin
            shift_reg  <= '0;
            dibit_cnt  <= '0;
            active     <= 1'b0;
            rmii_txd   <= '0;
            rmii_tx_en <= 1'b0;
        end else begin
            if (active) begin
                // Output current dibit (LSB-first)
                rmii_txd   <= shift_reg[1:0];
                rmii_tx_en <= 1'b1;
                shift_reg  <= {2'b00, shift_reg[7:2]};
                dibit_cnt  <= dibit_cnt + 1'b1;

                if (dibit_cnt == 2'd3) begin
                    // Last dibit of this byte
                    if (tx_valid) begin
                        // Next byte ready: seamless transition
                        shift_reg <= tx_data;
                        dibit_cnt <= '0;
                    end else begin
                        // No more data
                    end
                end
            end else begin
                // Idle: wait for new byte
                rmii_txd   <= '0;
                rmii_tx_en <= 1'b0;

                if (tx_valid) begin
                    shift_reg <= tx_data;
                    dibit_cnt <= '0;
                    active    <= 1'b1;
                end
            end
        end
    end

endmodule
