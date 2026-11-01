`timescale 1ns/1ps
// Both tile memories have one synchronous read cycle. rv/rl follow that read.
// Lane i is delayed i extra clocks: W[r,k] and X[k,c] meet at k+r+c.
module data_mapper(
    input wire clk,rst,start,linear_mode,
    input wire [7:0] k_count,
    output wire rd_en,
    output wire [7:0] rd_addr,
    input wire [127:0] act_data,input wire [63:0] weight_data,
    output wire [127:0] activations,output wire [63:0] weights,
    output wire [15:0] av,al,output wire [7:0] wv,wl,
    output reg [3:0] act_rd_ic,act_rd_tap
);
    reg feeding,rv,rl;
    reg [7:0] count;
    assign rd_en=feeding;
    assign rd_addr=count;
    always @(posedge clk) begin
        if(rst) begin feeding<=0;count<=0;rv<=0;rl<=0;act_rd_ic<=0;act_rd_tap<=0;end
        else if(start) begin feeding<=1;count<=0;rv<=0;rl<=0;act_rd_ic<=0;act_rd_tap<=0;end
        else begin
            rv<=rd_en; rl<=rd_en && count==k_count-1;
            if(feeding) begin
                if(count==k_count-1) feeding<=0;
                else begin
                    count<=count+1'b1;
                    if(act_rd_tap==8) begin act_rd_tap<=0;act_rd_ic<=act_rd_ic+1'b1;end
                    else act_rd_tap<=act_rd_tap+1'b1;
                end
            end
        end
    end
    genvar alane;
    generate for(alane=0;alane<16;alane=alane+1) begin: a_skew
        reg [7:0] d[0:alane];
        reg [alane:0] v,l;
        integer j;
        always @(posedge clk) begin
            if(rst || start) begin
                v<=0;l<=0;
                for(j=0;j<=alane;j=j+1) d[j]<=0;
            end else begin
                d[0]<=act_data[alane*8+:8];
                v[0]<=rv && (!linear_mode || alane==0);l[0]<=rl && (!linear_mode || alane==0);
                for(j=1;j<=alane;j=j+1) begin d[j]<=d[j-1];v[j]<=v[j-1];l[j]<=l[j-1];end
            end
        end
        assign activations[alane*8+:8]=d[alane];
        assign av[alane]=v[alane];assign al[alane]=l[alane];
    end endgenerate
    genvar wlane;
    generate for(wlane=0;wlane<8;wlane=wlane+1) begin: w_skew
        reg [7:0] d[0:wlane];
        reg [wlane:0] v,l;
        integer j;
        always @(posedge clk) begin
            if(rst || start) begin
                v<=0;l<=0;
                for(j=0;j<=wlane;j=j+1) d[j]<=0;
            end else begin
                d[0]<=weight_data[wlane*8+:8];
                v[0]<=rv && (!linear_mode || wlane<2);l[0]<=rl && (!linear_mode || wlane<2);
                for(j=1;j<=wlane;j=j+1) begin d[j]<=d[j-1];v[j]<=v[j-1];l[j]<=l[j-1];end
            end
        end
        assign weights[wlane*8+:8]=d[wlane];
        assign wv[wlane]=v[wlane];assign wl[wlane]=l[wlane];
    end endgenerate
endmodule
