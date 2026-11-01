`timescale 1ns/1ps
module tb_array;
    reg clk=0;always #5 clk=~clk;
    reg rst=1,start=0;reg [7:0] k_count;
    wire re;wire [7:0] addr;reg [127:0] ad;reg [63:0] wd;
    reg signed [7:0] x[0:143][0:15],wref[0:7][0:143];
    wire [127:0] a;wire [63:0] w;wire [15:0] av,al;wire [7:0] wv,wl;
    wire [4095:0] results;wire done;
    data_mapper mapper(.clk(clk),.rst(rst),.start(start),.linear_mode(1'b0),.k_count(k_count),.rd_en(re),.rd_addr(addr),
        .act_data(ad),.weight_data(wd),.activations(a),.weights(w),.av(av),.wv(wv),.al(al),.wl(wl));
    systolic_array array(.clk(clk),.rst(rst),.clear(start),.linear_mode(1'b0),.activations(a),.weights(w),.av(av),.wv(wv),.al(al),.wl(wl),.results(results),.done(done));
    integer i,j,k,t,cycles;reg signed [31:0] expected;
    always @(posedge clk) if(re) begin
        for(integer c=0;c<16;c=c+1) ad[c*8+:8]<=x[addr][c];
        for(integer r=0;r<8;r=r+1) wd[r*8+:8]<=wref[r][addr];
    end
    initial begin
        repeat(4) @(negedge clk);rst=0;
        for(t=0;t<6;t=t+1) begin
            case(t%3) 0:k_count=9;1:k_count=72;2:k_count=144;endcase
            for(k=0;k<144;k=k+1) for(i=0;i<16;i=i+1) begin
                x[k][i]=((k*17+i*31+t*7)%255)-127;
                if(i<8) wref[i][k]=((k*43+i*13+t*11)%255)-127;
            end
            @(negedge clk);start=1;
            @(negedge clk);start=0;
            cycles=0;
            while(!done && cycles<200) begin @(negedge clk);cycles=cycles+1;end
            if(!done) $fatal(1,"array timeout K=%0d",k_count);
            for(i=0;i<8;i=i+1) for(j=0;j<16;j=j+1) begin
                expected=0;
                for(k=0;k<k_count;k=k+1) expected=expected+$signed(x[k][j])*$signed(wref[i][k]);
                if($signed(results[(i*16+j)*32+:32])!==expected)
                    $fatal(1,"array mismatch t=%0d r=%0d c=%0d actual=%0d expected=%0d",t,i,j,$signed(results[(i*16+j)*32+:32]),expected);
            end
            $display("PASS array K=%0d signed outputs=128 latency=%0d",k_count,cycles);
            repeat(3) @(negedge clk);
        end
        $display("PASS tb_array");$finish;
    end
endmodule
