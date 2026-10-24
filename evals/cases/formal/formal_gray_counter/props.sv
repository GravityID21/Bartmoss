reg f_past_valid = 1'b0;
always @(posedge clk) f_past_valid <= 1'b1;
initial assume (rst);
always @* assert (gray == (bin ^ (bin >> 1)));
always @(posedge clk) if (f_past_valid && !$past(rst)) begin
    if ($past(en)) assert ($countones(gray ^ $past(gray)) == 1);
    else assert ($stable(gray));
    if ($past(en)) assert (bin == $past(bin) + 4'd1);
end
always @(posedge clk) if (f_past_valid && $past(rst)) assert (gray == 4'd0);
always @(posedge clk) cover (gray == 4'b1000);
