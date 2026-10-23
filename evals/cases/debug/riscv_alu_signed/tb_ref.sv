`timescale 1ns/1ps
module tb_ref;
    import riscv_pkg::*;
    logic [31:0] operand_a, operand_b, result;
    alu_op_e alu_op;
    logic zero;
    int errors = 0;

    riscv_alu dut (.*);

    task automatic check(input alu_op_e op, input logic [31:0] a, input logic [31:0] b, input logic [31:0] exp);
        operand_a = a; operand_b = b; alu_op = op; #1;
        if (result !== exp || zero !== (exp == 0)) begin
            $display("ERROR: %s a=%h b=%h: expected %h got %h (zero %b)", op.name(), a, b, exp, result, zero);
            errors++;
        end
    endtask

    initial begin
        check(ALU_ADD,  32'h7FFF_FFFF, 32'h1, 32'h8000_0000);
        check(ALU_SUB,  32'h0, 32'h1, 32'hFFFF_FFFF);
        check(ALU_SLL,  32'h1, 32'd4, 32'h10);
        check(ALU_SLL,  32'h1, 32'd33, 32'h2);             // only shamt = b[4:0] counts
        check(ALU_SRL,  32'h8000_0000, 32'd4, 32'h0800_0000);
        check(ALU_SRA,  32'h8000_0000, 32'd4, 32'hF800_0000);
        check(ALU_SRA,  32'h4000_0000, 32'd36, 32'h0400_0000);
        check(ALU_SLT,  32'hFFFF_FFFF, 32'h1, 32'h1);        // -1 < 1
        check(ALU_SLT,  32'h1, 32'hFFFF_FFFF, 32'h0);
        check(ALU_SLTU, 32'hFFFF_FFFF, 32'h1, 32'h0);
        check(ALU_XOR,  32'hF0F0_F0F0, 32'hFFFF_0000, 32'h0F0F_F0F0);
        check(ALU_AND,  32'hF0F0_F0F0, 32'h0F0F_0F0F, 32'h0);
        check(ALU_OR,   32'hF000_0000, 32'h0000_000F, 32'hF000_000F);
        check(ALU_PASSB,32'h1234_5678, 32'hDEAD_BEEF, 32'hDEAD_BEEF);
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
