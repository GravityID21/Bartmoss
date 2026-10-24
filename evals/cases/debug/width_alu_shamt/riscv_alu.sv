// riscv_alu.sv -- combinational ALU for RV32I.
// Covers ADD/SUB/AND/OR/XOR/SLL/SRL/SRA/SLT/SLTU plus a pass-B op used by LUI.
// Shift amount is operand_b[4:0] per RV32I spec.
module riscv_alu
    import riscv_pkg::*;
(
    input  logic    [XLEN-1:0] operand_a,
    input  logic    [XLEN-1:0] operand_b,
    input  alu_op_e            alu_op,
    output logic    [XLEN-1:0] result,
    output logic               zero
);

    logic [3:0]         shamt;
    logic signed [XLEN-1:0] a_signed;
    logic signed [XLEN-1:0] b_signed;

    assign shamt    = operand_b[3:0];
    assign a_signed = operand_a;
    assign b_signed = operand_b;

    always_comb begin
        unique case (alu_op)
            ALU_ADD:   result = operand_a + operand_b;
            ALU_SUB:   result = operand_a - operand_b;
            ALU_AND:   result = operand_a & operand_b;
            ALU_OR:    result = operand_a | operand_b;
            ALU_XOR:   result = operand_a ^ operand_b;
            ALU_SLL:   result = operand_a << shamt;
            ALU_SRL:   result = operand_a >> shamt;
            ALU_SRA:   result = XLEN'(a_signed >>> shamt);
            ALU_SLT:   result = (a_signed < b_signed) ? 32'd1 : 32'd0;
            ALU_SLTU:  result = (operand_a < operand_b) ? 32'd1 : 32'd0;
            ALU_PASSB: result = operand_b;
            default:   result = '0;
        endcase
    end

    assign zero = (result == '0);

endmodule : riscv_alu
