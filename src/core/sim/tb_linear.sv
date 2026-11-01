`timescale 1ns/1ps
module tb_linear;
    reg clk=0;always #5 clk=~clk;
    reg rst=1,start=0;
    wire re;wire [7:0] addr;reg [127:0] ad;reg [63:0] wd;
    wire [127:0] a;wire [63:0] w;wire [15:0] av,al;wire [7:0] wv,wl;
    wire [4095:0] results;wire done;wire [3:0] ic,tap;
    reg signed [7:0] x[0:31],wc[0:31],ws[0:31];
    reg signed [63:0] ec,es;integer trial,k,p,cycles;
    data_mapper mapper(.clk(clk),.rst(rst),.start(start),.linear_mode(1'b1),.k_count(8'd32),
        .rd_en(re),.rd_addr(addr),.act_data(ad),.weight_data(wd),.activations(a),.weights(w),
        .av(av),.wv(wv),.al(al),.wl(wl),.act_rd_ic(ic),.act_rd_tap(tap));
    systolic_array array(.clk(clk),.rst(rst),.clear(start),.linear_mode(1'b1),
        .activations(a),.weights(w),.av(av),.wv(wv),.al(al),.wl(wl),.results(results),.done(done));
    always @(posedge clk) if(re) begin
        if(addr!=ic*9+tap) $fatal(1,"Linear activation mapping");
        // Inactive data lanes contain nonzero garbage; only valid masks disable them.
        ad<={16{8'h7f}};ad[0+:8]<=x[addr];
        wd<={8{8'h81}};wd[0+:8]<=wc[addr];wd[8+:8]<=ws[addr];
    end
    initial begin
        repeat(3) @(negedge clk);rst=0;
        // Abort an in-flight dot product before testing complete signed vectors.
        start=1;@(negedge clk);start=0;repeat(10) @(negedge clk);
        rst=1;repeat(2) @(negedge clk);rst=0;
        repeat(10) begin @(negedge clk);if(done) $fatal(1,"Linear late done after reset");end
        for(trial=0;trial<4;trial=trial+1) begin
            ec=0;es=0;
            for(k=0;k<32;k=k+1) begin
                x[k]=(trial==0)?127:((trial==1)?-127:((k*13+trial*7)%255-127));
                wc[k]=(trial==0)?127:((trial==1)?127:((k*19+3)%255-127));
                ws[k]=(trial==0)?-127:((trial==1)?-127:((k*7+31)%255-127));
                ec=ec+$signed(x[k])*$signed(wc[k]);es=es+$signed(x[k])*$signed(ws[k]);
            end
            start=1;@(negedge clk);start=0;cycles=0;
            while(!done && cycles<100) begin @(negedge clk);cycles=cycles+1;end
            if(!done || $signed(results[0+:32])!==ec[31:0] || $signed(results[512+:32])!==es[31:0]) $fatal(1,"Linear signed dot product");
            for(p=0;p<128;p=p+1) if(p!=0 && p!=16 && results[p*32+:32]!==32'd0) $fatal(1,"inactive Linear PE");
            $display("PASS Linear trial=%0d K=32 latency=%0d collision_dot=%0d steering_dot=%0d",trial,cycles,ec,es);
            repeat(5) begin @(negedge clk);if(done) $fatal(1,"Linear repeated done");end
        end
        $display("PASS tb_linear: signed/extreme dot products, inactive PEs, K mapping, in-flight reset");$finish;
    end
endmodule
