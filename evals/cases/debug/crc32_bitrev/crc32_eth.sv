// crc32_eth.sv -- Ethernet CRC-32 calculator, byte-at-a-time
// Polynomial: 0x04C11DB7 (IEEE 802.3)
// Convention: CRC register initialized to all-1s, output inverted & bit-reversed
//
// Usage:
//   1. Assert init for one cycle to reset CRC state
//   2. Feed data_in bytes with valid=1
//   3. crc_out is the running CRC (valid after all data bytes processed)
//   4. For TX: append ~crc_out (bit-reversed) to frame
//   5. For RX: feed entire frame including FCS; residue == 32'hC704DD7B if OK

module crc32_eth (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        init,       // synchronous init: reset CRC to all-1s
    input  logic        valid,      // data byte valid
    input  logic [7:0]  data_in,    // input byte (LSbit first on wire)
    output logic [31:0] crc_out,    // current CRC value
    output logic        crc_match   // 1 when CRC residue matches magic value
);

    localparam [31:0] CRC_RESIDUE = 32'hC704DD7B;

    logic [31:0] crc_reg;
    logic [31:0] crc_next;

    // Bit-reverse the input byte (Ethernet sends LSbit first)
    logic [7:0] data_rev;
    always_comb begin
        for (int i = 0; i < 8; i++)
            data_rev[i] = data_in[7-i];
    end

    // Combinational CRC calculation: process one byte at a time
    // XOR data into top 8 bits of CRC, then shift through polynomial 8 times
    always_comb begin
        crc_next = crc_reg;
        crc_next[31:24] = crc_reg[31:24] ^ data_in;

        for (int i = 0; i < 8; i++) begin
            if (crc_next[31])
                crc_next = {crc_next[30:0], 1'b0} ^ 32'h04C11DB7;
            else
                crc_next = {crc_next[30:0], 1'b0};
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            crc_reg <= 32'hFFFF_FFFF;
        else if (init)
            crc_reg <= 32'hFFFF_FFFF;
        else if (valid)
            crc_reg <= crc_next;
    end

    // Output: invert and bit-reverse for wire format
    always_comb begin
        for (int i = 0; i < 32; i++)
            crc_out[i] = ~crc_reg[31-i];
    end

    assign crc_match = (crc_reg == CRC_RESIDUE);

endmodule
