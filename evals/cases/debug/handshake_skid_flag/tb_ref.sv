`timescale 1ns/1ps
module tb_ref;
    logic aclk = 0, aresetn = 0;
    axis_if #(.DATA_WIDTH(8)) s_if (.aclk(aclk), .aresetn(aresetn));
    axis_if #(.DATA_WIDTH(8)) m_if (.aclk(aclk), .aresetn(aresetn));
    axis_register_slice #(.DATA_WIDTH(8)) dut (.s_axis(s_if.slave), .m_axis(m_if.master));
    always #5 aclk = ~aclk;

    localparam int N = 200;
    int sent = 0, received = 0, errors = 0;
    int unsigned seed = 1;
    bit s_accepted;

    function automatic bit coin(input int pct);
        seed = seed * 1103515245 + 12345;
        return ((seed >> 16) % 100) < pct;
    endfunction

    // Sample handshakes on the clock edge (pre-update values).
    always @(posedge aclk) s_accepted <= aresetn && s_if.tvalid && s_if.tready;
    always @(posedge aclk) if (aresetn && m_if.tvalid && m_if.tready) begin
        if (m_if.tdata !== 8'(received * 7 + 3) || m_if.tlast !== (received % 5 == 4)) begin
            $display("ERROR: beat %0d: expected %h/%0b got %h/%0b", received, 8'(received * 7 + 3),
                     received % 5 == 4, m_if.tdata, m_if.tlast);
            errors++;
        end
        received++;
    end

    // Drive everything on the falling edge.
    initial begin
        s_if.tvalid = 0; s_if.tdata = 0; s_if.tkeep = 1; s_if.tlast = 0; s_if.tuser = 0;
        m_if.tready = 0;
        repeat (3) @(posedge aclk);
        @(negedge aclk) aresetn = 1;
        forever begin
            @(negedge aclk);
            if (s_accepted) begin sent++; s_if.tvalid = 0; end
            if (!s_if.tvalid && sent < N && coin(70)) begin
                s_if.tvalid = 1;
                s_if.tdata  = 8'(sent * 7 + 3);
                s_if.tlast  = (sent % 5 == 4);
            end
            m_if.tready = coin(60);
        end
    end

    initial begin
        #100000 $display("TEST FAILED: timeout (received %0d of %0d)", received, N); $finish;
    end
    initial begin
        wait (received == N);
        #1;
        if (errors == 0) $display("TEST PASSED"); else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
