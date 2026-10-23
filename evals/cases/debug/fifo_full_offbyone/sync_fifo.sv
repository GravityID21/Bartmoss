// Synchronous first-word-fall-through FIFO. DEPTH must be a power of two.
module sync_fifo #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 4
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             wr_en,
    input  logic [WIDTH-1:0] wr_data,
    input  logic             rd_en,
    output logic [WIDTH-1:0] rd_data,
    output logic             full,
    output logic             empty
);
    localparam int AW = $clog2(DEPTH);

    logic [WIDTH-1:0] mem [DEPTH];
    logic [AW-1:0]    wr_ptr, rd_ptr;
    logic [AW:0]      count;

    wire do_wr = wr_en && !full;
    wire do_rd = rd_en && !empty;

    always_ff @(posedge clk) begin
        if (do_wr) mem[wr_ptr] <= wr_data;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= '0;
            rd_ptr <= '0;
            count  <= '0;
        end else begin
            if (do_wr) wr_ptr <= wr_ptr + 1'b1;
            if (do_rd) rd_ptr <= rd_ptr + 1'b1;
            case ({do_wr, do_rd})
                2'b10:   count <= count + 1'b1;
                2'b01:   count <= count - 1'b1;
                default: ;
            endcase
        end
    end

    assign rd_data = mem[rd_ptr];
    assign full    = (count == DEPTH - 1);
    assign empty   = (count == 0);
endmodule
