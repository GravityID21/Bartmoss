// Skid buffer with a registered s_ready: a main output register plus one skid register. Words are
// delivered in order with none lost or duplicated; m_valid and m_data hold while m_ready is low.
module skid (
    input  logic       clk,
    input  logic       rst,
    input  logic       s_valid,
    output logic       s_ready,
    input  logic [7:0] s_data,
    output logic       m_valid,
    input  logic       m_ready,
    output logic [7:0] m_data
);
    logic       skid_valid;
    logic [7:0] skid_data;

    assign s_ready = !skid_valid;

    always_ff @(posedge clk) begin
        if (rst) begin
            m_valid <= 1'b0;
            skid_valid <= 1'b0;
        end else begin
            if (s_valid && s_ready && m_valid && !m_ready) begin
                skid_valid <= 1'b1;
                skid_data <= s_data;
            end
            if (!m_valid || m_ready) begin
                if (skid_valid) begin
                    m_data <= skid_data;
                    m_valid <= 1'b1;
                    skid_valid <= 1'b0;
                end else begin
                    m_valid <= s_valid;
                    if (s_valid) m_data <= s_data;
                end
            end
        end
    end
endmodule
