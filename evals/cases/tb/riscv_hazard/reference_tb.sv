// Hidden reference testbench used only to confirm the mutants are detectable. Never shown to the model.
`timescale 1ns/1ps
module tb_riscv_hazard_unit;
    import riscv_pkg::*;
    logic [4:0] if_id_rs1_addr, if_id_rs2_addr, id_ex_rd_addr, id_ex_rs1_addr, id_ex_rs2_addr;
    logic [4:0] ex_mem_rd_addr, mem_wb_rd_addr;
    logic id_ex_mem_read, ex_mem_reg_write, mem_wb_reg_write, load_use_stall;
    fwd_sel_e forward_a, forward_b;
    int errors = 0;
    int unsigned seed = 7;

    riscv_hazard_unit dut (.*);

    function automatic int unsigned rnd(input int unsigned n);
        seed = seed * 1103515245 + 12345;
        return (seed >> 16) % n;
    endfunction

    function automatic fwd_sel_e expect_fwd(input logic [4:0] rs);
        if (ex_mem_reg_write && ex_mem_rd_addr != 0 && ex_mem_rd_addr == rs) return FWD_EX_MEM;
        if (mem_wb_reg_write && mem_wb_rd_addr != 0 && mem_wb_rd_addr == rs) return FWD_MEM_WB;
        return FWD_NONE;
    endfunction

    initial begin
        logic exp_stall;
        for (int i = 0; i < 4000; i++) begin
            // Register numbers 0-3 make hazards, double hazards and x0 cases common.
            if_id_rs1_addr = 5'(rnd(4));  if_id_rs2_addr = 5'(rnd(4));
            id_ex_rd_addr  = 5'(rnd(4));  id_ex_rs1_addr = 5'(rnd(4));  id_ex_rs2_addr = 5'(rnd(4));
            ex_mem_rd_addr = 5'(rnd(4));  mem_wb_rd_addr = 5'(rnd(4));
            id_ex_mem_read = rnd(2) == 1; ex_mem_reg_write = rnd(2) == 1; mem_wb_reg_write = rnd(2) == 1;
            #1;
            exp_stall = id_ex_mem_read && id_ex_rd_addr != 0 &&
                        (id_ex_rd_addr == if_id_rs1_addr || id_ex_rd_addr == if_id_rs2_addr);
            if (load_use_stall !== exp_stall || forward_a !== expect_fwd(id_ex_rs1_addr) ||
                forward_b !== expect_fwd(id_ex_rs2_addr)) begin
                if (errors < 5)
                    $display("ERROR: vector %0d: expected stall=%0b a=%s b=%s got stall=%0b a=%s b=%s", i, exp_stall,
                             expect_fwd(id_ex_rs1_addr).name(), expect_fwd(id_ex_rs2_addr).name(), load_use_stall,
                             forward_a.name(), forward_b.name());
                errors++;
            end
        end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
