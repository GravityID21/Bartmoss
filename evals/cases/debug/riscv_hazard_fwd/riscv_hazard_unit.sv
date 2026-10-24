// riscv_hazard_unit.sv -- forwarding + load-use stall.
// Stage 3. Branch / JALR / JAL flushes are handled in riscv_top where the
// pipeline registers and PC live; this module owns data-hazard logic only.
module riscv_hazard_unit
    import riscv_pkg::*;
(
    // ID stage (raw rs1/rs2 of the instruction currently in IF/ID).
    input  logic [REG_ADDR_W-1:0] if_id_rs1_addr,
    input  logic [REG_ADDR_W-1:0] if_id_rs2_addr,

    // EX stage (ID/EX contents).
    input  logic                  id_ex_mem_read,
    input  logic [REG_ADDR_W-1:0] id_ex_rd_addr,
    input  logic [REG_ADDR_W-1:0] id_ex_rs1_addr,
    input  logic [REG_ADDR_W-1:0] id_ex_rs2_addr,

    // MEM stage (EX/MEM contents).
    input  logic                  ex_mem_reg_write,
    input  logic [REG_ADDR_W-1:0] ex_mem_rd_addr,

    // WB stage (MEM/WB contents).
    input  logic                  mem_wb_reg_write,
    input  logic [REG_ADDR_W-1:0] mem_wb_rd_addr,

    output logic                  load_use_stall,
    output fwd_sel_e              forward_a,
    output fwd_sel_e              forward_b
);

    always_comb begin
        // A load in EX feeding the instruction in ID (whose rs1/rs2 is the
        // load's rd) cannot be forwarded -- the data isn't ready until end
        // of MEM. Insert one bubble.
        load_use_stall = id_ex_mem_read && (id_ex_rd_addr != '0) &&
                         ((id_ex_rd_addr == if_id_rs1_addr) ||
                          (id_ex_rd_addr == if_id_rs2_addr));

        // Forward A (rs1 of instruction in EX).
        if (ex_mem_reg_write && (ex_mem_rd_addr != '0) &&
            (ex_mem_rd_addr == id_ex_rs1_addr))
            forward_a = FWD_EX_MEM;
        else if (mem_wb_reg_write && (mem_wb_rd_addr != '0) &&
                 (mem_wb_rd_addr == id_ex_rs2_addr))
            forward_a = FWD_MEM_WB;
        else
            forward_a = FWD_NONE;

        // Forward B (rs2 of instruction in EX).
        if (ex_mem_reg_write && (ex_mem_rd_addr != '0) &&
            (ex_mem_rd_addr == id_ex_rs2_addr))
            forward_b = FWD_EX_MEM;
        else if (mem_wb_reg_write && (mem_wb_rd_addr != '0) &&
                 (mem_wb_rd_addr == id_ex_rs2_addr))
            forward_b = FWD_MEM_WB;
        else
            forward_b = FWD_NONE;
    end

endmodule : riscv_hazard_unit
