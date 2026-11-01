`timescale 1ns/1ps
module tb_memory;
    reg clk=0;always #5 clk=~clk;
    reg rst=1,wr_en=0,rd_en=0;
    reg [6:0] wr_width=64,col_width=64;
    reg [12:0] wr_addr=0,rd_addr=0;
    reg [7:0] wr_data=0;
    wire [7:0] rd_data;
    reg [3:0] col_en=0;
    reg [51:0] col_addr=0;
    wire [31:0] col_data;
    feature_bram dut(.*);
    integer w,i,j,lane,base,checks=0;
    function [7:0] value_at(input integer addr,input integer width);
        value_at=((addr*37)^(addr>>3)^(addr>>8)^(width*11));
    endfunction
    initial begin
        repeat(3) @(negedge clk);rst=0;
        for(w=64;w>=8;w=w/2) begin
            wr_width=w;col_width=w;
            // Exercise the entire address range, including channel and bank boundaries.
            for(i=0;i<8192;i=i+1) begin
                @(negedge clk);wr_en=1;wr_addr=i;wr_data=value_at(i,w);
            end
            @(negedge clk);wr_en=0;
            // Task reset retains both stored bytes and their layout metadata.
            rst=1;repeat(2) @(negedge clk);rst=0;
            for(i=8191;i>=0;i=i-1) begin
                @(negedge clk);rd_en=1;rd_addr=i;
                @(posedge clk);#1;
                if(rd_data!==value_at(i,w)) $fatal(1,"scalar read width=%0d addr=%0d",w,i);
                checks=checks+1;
            end
            @(negedge clk);rd_en=0;
            // Four successive rows: all rotations, non-row-aligned bases, continuous requests.
            for(base=0;base<4*w;base=base+1) begin
                @(negedge clk);col_en=15;
                for(lane=0;lane<4;lane=lane+1) col_addr[lane*13+:13]=base+lane*w;
                @(posedge clk);#1;
                for(lane=0;lane<4;lane=lane+1) begin
                    if(col_data[lane*8+:8]!==value_at(base+lane*w,w))
                        $fatal(1,"column width=%0d base=%0d lane=%0d",w,base,lane);
                    checks=checks+1;
                end
            end
            // Exercise every enable mask; disabled lane values are don't-care.
            for(j=0;j<16;j=j+1) begin
                @(negedge clk);col_en=j;
                for(lane=0;lane<4;lane=lane+1) col_addr[lane*13+:13]=7001+lane*w;
                @(posedge clk);#1;
                for(lane=0;lane<4;lane=lane+1) if(col_en[lane]) begin
                    if(col_data[lane*8+:8]!==value_at(7001+lane*w,w)) $fatal(1,"masked read");
                    checks=checks+1;
                end
            end
            @(negedge clk);col_en=0;
            $display("PASS memory width=%0d entire scalar range and four-row rotations/masks",w);
        end
        $display("PASS tb_memory: %0d exact byte comparisons",checks);$finish;
    end
    initial begin #2000000;$fatal(1,"memory watchdog");end
endmodule
