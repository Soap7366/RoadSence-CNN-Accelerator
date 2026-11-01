`timescale 1ns/1ps
module tb_post;
    reg clk=0;always #5 clk=~clk;
    reg rst=1,start=0;
    reg [255:0] biases=0,multipliers=0;
    wire re,we,done,dv;wire [4:0] ra;wire [6:0] di;wire [12:0] wa;
    wire [7:0] wd;wire [127:0] da;reg [127:0] rd;
    reg signed [31:0] data[0:127],expected_acc[0:127];
    reg signed [7:0] expected_q[0:127];reg [7:0] expected_pool[0:31];
    reg signed [63:0] a,p,m,rounded;
    integer i,j,nq=0,na=0,nw=0,cycles=0,address,qi;
    integer nread=0,tick_count=0,last_read=0,last_quant=0,last_write=0,trial,lane;
    bit checking=0;
    post_processing dut(.clk(clk),.rst(rst),.start(start),.tile_y(7'd6),.tile_x(7'd4),
        .height(7'd16),.width(7'd16),.oc_base(6'd24),.output_base(13'd64),
        .biases(biases),.multipliers(multipliers),.rd_en(re),.rd_addr(ra),.rd_data(rd),
        .wr_en(we),.wr_addr(wa),.wr_data(wd),.done(done),.debug_valid(dv),.debug_acc(da),.debug_index(di));
    always @(posedge clk) begin
        tick_count=tick_count+1;
        if(re) for(integer l=0;l<4;l=l+1) rd[l*32+:32]<=data[ra*4+l];
        if(!rst && checking) begin
            if(re) begin
                if(ra!==nread[4:0] || (nread>0 && tick_count!=last_read+1)) $fatal(1,"Post group read initiation interval is not one");
                last_read=tick_count;nread=nread+1;
            end
            if(dv) begin
                if(di!==na[6:0]) $fatal(1,"post group tag mismatch");
                for(lane=0;lane<4;lane=lane+1)
                    if(da[lane*32+:32]!==expected_acc[di+lane]) $fatal(1,"post lane bias mismatch");
                na=na+4;
            end
            // Observe the signed requant result before ReLU destroys negative values.
            if(dut.quant_valid) begin
                if(dut.quant_index*4!=nq) $fatal(1,"requant group tag mismatch");
                for(lane=0;lane<4;lane=lane+1)
                    if($signed(dut.q[lane])!==expected_q[nq+lane])
                        $fatal(1,"requant mismatch index=%0d got=%0d expected=%0d",nq+lane,$signed(dut.q[lane]),expected_q[nq+lane]);
                if(nq>0 && tick_count!=last_quant+1) $fatal(1,"Post quant pipeline has bubbles");
                last_quant=tick_count;
                nq=nq+4;
            end
            if(we) begin
                address=64+(24+nw/4)*64+3*8+2+nw%4;
                if(wd!==expected_pool[nw] || wa!==address[12:0]) $fatal(1,"post pool/address mismatch");
                if(nw>0 && tick_count!=last_write+1) $fatal(1,"Post pool write interval is not one");
                last_write=tick_count;
                nw=nw+1;
            end
        end
    end
    initial begin
        for(i=0;i<8;i=i+1) begin
            case(i)
                0,1:multipliers[i*32+:32]=32'd1073741824;
                2:multipliers[i*32+:32]=32'd2147483647;
                3:multipliers[i*32+:32]=32'd742626;
                4:multipliers[i*32+:32]=32'd128780396;
                5:multipliers[i*32+:32]=32'd1;
                6:multipliers[i*32+:32]=32'd0;
                7:multipliers[i*32+:32]=32'd15423668;
            endcase
            biases[i*32+:32]=(i==7)?-123:0;
            for(j=0;j<16;j=j+1) begin
                case(j%8)
                    0:data[i*16+j]=-3;1:data[i*16+j]=-1;2:data[i*16+j]=1;3:data[i*16+j]=3;
                    4:data[i*16+j]=-255;5:data[i*16+j]=255;
                    6:data[i*16+j]=-2147483000;7:data[i*16+j]=2147483000;
                endcase
            end
        end
        repeat(4) @(negedge clk);rst=0;start=1;
        @(negedge clk);start=0;
        cycles=0;
        while(!dut.write_valid && cycles<30) begin @(negedge clk);cycles=cycles+1;end
        if(!dut.write_valid || !dut.feeding || !dut.quant_valid) $fatal(1,"Post did not fill pipeline through pending write");
        rst=1;repeat(3) @(negedge clk);
        if(re || we || done || dut.quant_valid || dut.active) $fatal(1,"Post reset did not kill in-flight tokens");
        rst=0;
        for(trial=0;trial<4;trial=trial+1) begin
            if(trial==1) begin
                for(i=0;i<128;i=i+1) data[i]=-data[i];
                biases[7*32+:32]=32'sd45;
            end
            if(trial>=2) begin
                // Every lane must win a pool; values differ between all groups.
                for(i=0;i<8;i=i+1) begin
                    multipliers[i*32+:32]=32'd1073741824;
                    biases[i*32+:32]=i-4;
                end
                for(i=0;i<128;i=i+1)
                    data[i]=((i%4)==((i/4+trial)%4)) ? 100+i/4 : -20+(i%4);
            end
            for(i=0;i<32;i=i+1) expected_pool[i]=0;
            for(i=0;i<128;i=i+1) begin
            a=$signed(data[i])+$signed(biases[(i/16)*32+:32]);
            expected_acc[i]=a;
            m=$signed(multipliers[(i/16)*32+:32]);p=a*m;
            rounded=((p<0?-p:p)+64'sd1073741824)/64'sd2147483648;
            if(p<0) rounded=-rounded;
            if(rounded>127) rounded=127;
            if(rounded< -127) rounded=-127;
            expected_q[i]=rounded;
            qi=rounded<0?0:rounded;
            if(qi>expected_pool[i/4]) expected_pool[i/4]=qi;
            end
            nq=0;na=0;nw=0;nread=0;cycles=0;checking=1;
            @(negedge clk);start=1;@(negedge clk);start=0;
            while(!done && cycles<60) begin
                @(negedge clk);cycles=cycles+1;
                // A repeated command while active must not restart the group.
                if(cycles==5) start=1;
                if(cycles==6) start=0;
            end
            if(!done || nq!=128 || na!=128 || nw!=32 || nread!=32) $fatal(1,"post incomplete/too slow");
            if(cycles!=41 || we || dut.active) $fatal(1,"Post completion/last write commit mismatch");
            checking=0;
            $display("PASS Post tile=%0d cycles=%0d group_II=1 writes=32 interval=1",trial,cycles);
            repeat(3) begin
                @(negedge clk);
                if(re || we || done || dv || dut.quant_valid || dut.pair_valid)
                    $fatal(1,"Post leaked tokens after completion");
            end
        end
        $display("PASS tb_post: pipelined half-away rounding, +/- saturation, zero, bias/tag, pool/address, repeat and in-flight reset");$finish;
    end
endmodule
