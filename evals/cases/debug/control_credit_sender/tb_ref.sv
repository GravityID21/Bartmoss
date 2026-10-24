`timescale 1ns/1ps
module tb_ref;
    localparam int INIT = 4;
    logic aclk = 0, aresetn = 0, s_valid = 0, credit_return = 0, s_ready, m_valid;
    logic [7:0] s_data = 0, m_data, credits_q;
    int credits = INIT, outstanding = 0, errors = 0, simultaneous = 0;
    int unsigned seed = 3;

    credit_sender #(.DATA_WIDTH(8), .CREDIT_WIDTH(8), .INITIAL_CREDITS(INIT)) dut (.*);
    always #5 aclk = ~aclk;

    function automatic int unsigned rnd(input int unsigned n);
        seed = seed * 1103515245 + 12345;
        return (seed >> 16) % n;
    endfunction

    // Drive on the falling edge, check outputs, then advance the model at the rising edge.
    task automatic step(input logic valid, input logic ret);
        logic fire;
        @(negedge aclk);
        s_valid = valid; s_data = s_data + 1; credit_return = ret;
        #1;
        if (m_valid !== (valid && credits > 0) || s_ready !== (credits > 0) || credits_q !== 8'(credits)) begin
            if (errors < 5)
                $display("ERROR: credits=%0d valid=%0b ret=%0b: expected m_valid=%0b s_ready=%0b got m_valid=%0b s_ready=%0b credits_q=%0d",
                         credits, valid, ret, valid && credits > 0, credits > 0, m_valid, s_ready, credits_q);
            errors++;
        end
        @(posedge aclk);
        fire = valid && credits > 0;
        if (fire && ret) simultaneous++;
        credits += int'(ret) - int'(fire);
        outstanding += int'(fire) - int'(ret);
    endtask

    initial begin
        repeat (2) @(negedge aclk);
        aresetn = 1;
        // Drain every credit with no returns: the last credit must still be usable, then sending stops.
        repeat (INIT + 3) step(1, 0);
        // Return them one at a time.
        while (outstanding > 0) step(0, 1);
        // Random traffic with returns only for beats actually sent.
        for (int i = 0; i < 400; i++) step(rnd(100) < 60, outstanding > 0 && rnd(100) < 45);
        if (simultaneous == 0) begin $display("ERROR: no simultaneous send and return exercised"); errors++; end
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
