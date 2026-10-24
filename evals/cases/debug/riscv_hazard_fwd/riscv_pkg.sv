// riscv_pkg.sv -- RV32I common types, opcodes, ALU ops, pipeline-register structs.
package riscv_pkg;

    parameter int XLEN       = 32;
    parameter int REG_ADDR_W = 5;

    // Base opcodes (inst[6:0]) for RV32I.
    typedef enum logic [6:0] {
        OP_LUI     = 7'b0110111,
        OP_AUIPC   = 7'b0010111,
        OP_JAL     = 7'b1101111,
        OP_JALR    = 7'b1100111,
        OP_BRANCH  = 7'b1100011,
        OP_LOAD    = 7'b0000011,
        OP_STORE   = 7'b0100011,
        OP_OP_IMM  = 7'b0010011,
        OP_OP      = 7'b0110011,
        OP_MISCMEM = 7'b0001111,
        OP_SYSTEM  = 7'b1110011
    } opcode_e;

    // funct3 within OP / OP-IMM (ALU).
    typedef enum logic [2:0] {
        F3_ADD_SUB = 3'b000,
        F3_SLL     = 3'b001,
        F3_SLT     = 3'b010,
        F3_SLTU    = 3'b011,
        F3_XOR     = 3'b100,
        F3_SRL_SRA = 3'b101,
        F3_OR      = 3'b110,
        F3_AND     = 3'b111
    } f3_alu_e;

    // funct3 within BRANCH.
    typedef enum logic [2:0] {
        F3_BEQ  = 3'b000,
        F3_BNE  = 3'b001,
        F3_BLT  = 3'b100,
        F3_BGE  = 3'b101,
        F3_BLTU = 3'b110,
        F3_BGEU = 3'b111
    } f3_br_e;

    // funct3 within LOAD.
    typedef enum logic [2:0] {
        F3_LB  = 3'b000,
        F3_LH  = 3'b001,
        F3_LW  = 3'b010,
        F3_LBU = 3'b100,
        F3_LHU = 3'b101
    } f3_ld_e;

    // funct3 within STORE.
    typedef enum logic [2:0] {
        F3_SB = 3'b000,
        F3_SH = 3'b001,
        F3_SW = 3'b010
    } f3_st_e;

    // funct3 within SYSTEM. PRIV (000) is ECALL/EBREAK/MRET (imm[11:0]
    // distinguishes them). The remaining funct3 values are the Zicsr
    // CSR ops: rs1-source (W/S/C) and uimm5-source (WI/SI/CI).
    typedef enum logic [2:0] {
        F3_PRIV    = 3'b000,
        F3_CSRRW   = 3'b001,
        F3_CSRRS   = 3'b010,
        F3_CSRRC   = 3'b011,
        F3_CSRRWI  = 3'b101,
        F3_CSRRSI  = 3'b110,
        F3_CSRRCI  = 3'b111
    } f3_sys_e;

    // CSR operation selector (Stretch S.d). The "imm" flag (separate ctrl
    // bit) picks between rs1_data and zimm5 as the source value.
    typedef enum logic [1:0] {
        CSR_RW = 2'd0,
        CSR_RS = 2'd1,
        CSR_RC = 2'd2
    } csr_op_e;

    // ALU operation selector used by riscv_alu.
    typedef enum logic [3:0] {
        ALU_ADD  = 4'd0,
        ALU_SUB  = 4'd1,
        ALU_SLL  = 4'd2,
        ALU_SLT  = 4'd3,
        ALU_SLTU = 4'd4,
        ALU_XOR  = 4'd5,
        ALU_SRL  = 4'd6,
        ALU_SRA  = 4'd7,
        ALU_OR   = 4'd8,
        ALU_AND  = 4'd9,
        // Pass-through: result = operand B (used by LUI; AUIPC uses ADD with PC).
        ALU_PASSB = 4'd10
    } alu_op_e;

    // Immediate types produced by decoder (per RV32I encoding).
    typedef enum logic [2:0] {
        IMM_I = 3'd0,
        IMM_S = 3'd1,
        IMM_B = 3'd2,
        IMM_U = 3'd3,
        IMM_J = 3'd4,
        IMM_NONE = 3'd5
    } imm_type_e;

    // Memory access width / sign for loads and stores.
    typedef enum logic [2:0] {
        MEM_B  = 3'd0,  // signed byte
        MEM_H  = 3'd1,  // signed half
        MEM_W  = 3'd2,  // word
        MEM_BU = 3'd3,  // unsigned byte
        MEM_HU = 3'd4   // unsigned half
    } mem_size_e;

    // Writeback source selector.
    typedef enum logic [1:0] {
        WB_ALU = 2'd0,
        WB_MEM = 2'd1,
        WB_PC4 = 2'd2
    } wb_sel_e;

    // Decoded control bundle -- consumed by EX / MEM / WB stages.
    typedef struct packed {
        logic      reg_write;
        wb_sel_e   wb_sel;
        logic      alu_src_a_pc;   // 1 => operand_a = PC  (AUIPC)
        logic      alu_src_b_imm;  // 1 => operand_b = imm (ALU-imm, LOAD, STORE, LUI, AUIPC, JALR)
        alu_op_e   alu_op;
        logic      mem_read;
        logic      mem_write;
        mem_size_e mem_size;
        logic      branch;         // conditional branch
        logic      jump;           // JAL
        logic      jalr;           // JALR
        logic [2:0] br_op;         // branch funct3
        logic      illegal;
        imm_type_e imm_type;
        // M-extension (Stretch S.a). When `muldiv` is high, the EX stage
        // routes operands through `riscv_muldiv` instead of the ALU and
        // stalls the pipeline until the result is ready. `muldiv_op` is
        // the funct3 field (0..7 selects MUL/MULH/MULHSU/MULHU/DIV/DIVU/REM/REMU).
        logic       muldiv;
        logic [2:0] muldiv_op;
        // Zicsr + privileged ops (Stretch S.d). is_csr drives the EX-stage
        // CSR read/modify path; csr_op picks RW/RS/RC; csr_imm picks
        // zimm5 (instr[19:15]) vs rs1_data as the source value.
        // is_ecall / is_ebreak / is_mret split out the privileged
        // SYSTEM opcodes that used to all collapse onto `halt`.
        logic       is_csr;
        csr_op_e    csr_op;
        logic       csr_imm;
        logic       is_ecall;
        logic       is_ebreak;
        logic       is_mret;
    } ctrl_t;

    // -------- Pipeline registers (Stage 3) --------

    typedef struct packed {
        logic [XLEN-1:0]       pc;
        logic [XLEN-1:0]       pc_plus_4;
        logic [XLEN-1:0]       instr;
        // Prediction made at IF for *this* instruction's PC. Carried so
        // EX can compare against actual outcome and compute mispredict.
        logic                  pred_taken;
        logic [XLEN-1:0]       pred_target;
        // S.e: gshare GHR snapshot used by *this* prediction. Carried
        // so the EX-stage PHT update writes the same entry the
        // prediction read (= update_pc[7:2] XOR pred_ghr).
        logic [5:0]            pred_ghr;
        logic                  valid;
    } if_id_t;

    typedef struct packed {
        logic [XLEN-1:0]       pc;
        logic [XLEN-1:0]       pc_plus_4;
        ctrl_t                 ctrl;
        logic [XLEN-1:0]       rs1_data;
        logic [XLEN-1:0]       rs2_data;
        logic [XLEN-1:0]       imm;
        logic [REG_ADDR_W-1:0] rs1_addr;
        logic [REG_ADDR_W-1:0] rs2_addr;
        logic [REG_ADDR_W-1:0] rd_addr;
        logic                  pred_taken;
        logic [XLEN-1:0]       pred_target;
        logic [5:0]            pred_ghr;       // S.e: gshare snapshot
        logic                  valid;
    } id_ex_t;

    // Trimmed control bits carried past EX. Note: `halt` is gone -- with
    // real traps (S.d), EBREAK and ECALL redirect via mtvec instead of
    // latching a halt output. The harness terminates by watching tohost
    // writes on the data bus, not by sampling a `halted` pin.
    typedef struct packed {
        logic [XLEN-1:0]       pc_plus_4;
        logic                  reg_write;
        wb_sel_e               wb_sel;
        logic                  mem_read;
        logic                  mem_write;
        mem_size_e             mem_size;
        logic [XLEN-1:0]       alu_result;
        logic [XLEN-1:0]       store_data;
        logic [REG_ADDR_W-1:0] rd_addr;
        logic                  valid;
    } ex_mem_t;

    typedef struct packed {
        logic [XLEN-1:0]       pc_plus_4;
        logic                  reg_write;
        wb_sel_e               wb_sel;
        logic [XLEN-1:0]       alu_result;
        logic [XLEN-1:0]       load_data;
        logic [REG_ADDR_W-1:0] rd_addr;
        logic                  valid;
    } mem_wb_t;

    // Forwarding-mux select.
    typedef enum logic [1:0] {
        FWD_NONE   = 2'b00,
        FWD_MEM_WB = 2'b01,
        FWD_EX_MEM = 2'b10
    } fwd_sel_e;

endpackage : riscv_pkg
