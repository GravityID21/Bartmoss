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
            $display("ERROR: %s a=%h b=%h: expected %h got %h", op.name(), a, b, exp, result);
            errors++;
        end
    endtask

    initial begin
        check(ALU_ADD,  32'h0000_0001, 32'h1, 32'h2);
        check(ALU_SLL,  32'h1, 32'd4, 32'h10);
        check(ALU_SLL,  32'h1, 32'd20, 32'h0010_0000);
        check(ALU_SLL,  32'h1, 32'd31, 32'h8000_0000);
        check(ALU_SRL,  32'h8000_0000, 32'd16, 32'h0000_8000);
        check(ALU_SRL,  32'hFFFF_FFFF, 32'd28, 32'h0000_000F);
        check(ALU_SRA,  32'h8000_0000, 32'd31, 32'hFFFF_FFFF);
        check(ALU_SRA,  32'h4000_0000, 32'd17, 32'h0000_2000);
        check(ALU_SLT,  32'hFFFF_FFFF, 32'h1, 32'h1);
        check(ALU_SLTU, 32'hFFFF_FFFF, 32'h1, 32'h0);
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
