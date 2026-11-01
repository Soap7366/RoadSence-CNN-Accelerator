`timescale 1ns/1ps
module tb_gap;
    reg clk=0;always #5 clk=~clk;
    reg rst=1,clear=0,enable=1,wr_en=0;
    reg [12:0] wr_addr=0;reg [7:0] wr_data=0;reg [31:0] multiplier=90035185;
    wire ready,error,sum_valid,value_valid;wire [255:0] features;
    wire [4:0] sum_channel,value_channel;wire [31:0] sum_value;wire [7:0] value_data;
    online_gap dut(.*);
    integer trial,c,k,sums_seen,values_seen,cycles;
    integer expected[0:31];reg [31:0] seen_sums,seen_values;
    reg checking=0;reg signed [63:0] scaled,rounded;
    always @(posedge clk) if(checking && !rst && !clear) begin
        if(sum_valid) begin
            if(seen_sums[sum_channel] || sum_value!==expected[sum_channel]) $fatal(1,"unit GAP final sum");
            seen_sums[sum_channel]=1;sums_seen=sums_seen+1;
        end
        if(value_valid) begin
            scaled=expected[value_channel];scaled=scaled*$signed({1'b0,multiplier});
            rounded=(scaled+64'd1073741824)>>>31;if(rounded>127) rounded=127;
            if(seen_values[value_channel] || value_data!==rounded[7:0]) $fatal(1,"unit GAP rounding/saturation");
            seen_values[value_channel]=1;values_seen=values_seen+1;
        end
        if(ready && values_seen!=32) $fatal(1,"GAP ready before all values commit");
    end
    initial begin
        repeat(3) @(negedge clk);rst=0;
        for(trial=0;trial<2;trial=trial+1) begin
            clear=1;@(negedge clk);clear=0;
            multiplier=(trial==0)?90035185:32'h40000000;
            for(c=0;c<32;c=c+1) expected[c]=0;
            sums_seen=0;values_seen=0;seen_sums=0;seen_values=0;checking=1;
            // Interleaved channels make 32 final sums arrive on consecutive clocks.
            for(k=0;k<64;k=k+1) for(c=0;c<32;c=c+1) begin
                @(negedge clk);wr_en=1;wr_addr=c*64+k;
                if(trial==0) wr_data=(c+k)%128;
                else if(c%4==0) wr_data=0;
                else if(c%4==1) wr_data=(k==63)?1:0;
                else if(c%4==2) wr_data=(k==63)?3:0;
                else wr_data=127;
                expected[c]=expected[c]+wr_data;
            end
            @(negedge clk);wr_en=0;cycles=0;
            while(!ready && cycles<20) begin @(negedge clk);cycles=cycles+1;end
            // ready and the final debug token rise together; observe that token next edge.
            @(negedge clk);
            if(!ready || error || sums_seen!=32 || values_seen!=32) $fatal(1,"unit GAP coverage ready=%0d error=%0d sums=%0d values=%0d",ready,error,sums_seen,values_seen);
            checking=0;$display("PASS GAP trial=%0d 32 sums/values, II=1 final channels, last byte, half ties and saturation",trial);
        end
        // Duplicate channel writes are rejected, then clear recovers the unit.
        @(negedge clk);wr_en=1;wr_addr=0;wr_data=1;
        @(negedge clk);wr_en=0;if(!error) $fatal(1,"GAP duplicate not detected");
        clear=1;@(negedge clk);clear=0;if(error || ready) $fatal(1,"GAP clear recovery");
        // Reset while a completed channel is in the requantization pipeline.
        for(k=0;k<64;k=k+1) begin @(negedge clk);wr_en=1;wr_addr=k;wr_data=127;end
        @(negedge clk);wr_en=0;rst=1;repeat(2) @(negedge clk);rst=0;
        repeat(8) begin @(negedge clk);if(ready || sum_valid || value_valid || error) $fatal(1,"GAP late token after reset");end
        $display("PASS tb_gap: exact sums/quantization, interleave, ties, saturation, duplicate guard, clear/reset");$finish;
    end
endmodule
