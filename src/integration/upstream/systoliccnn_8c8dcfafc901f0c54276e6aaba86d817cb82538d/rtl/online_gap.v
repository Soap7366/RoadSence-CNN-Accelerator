`timescale 1ns/1ps
// Tap the COMMITTED Conv3 pool writes. No Feature RAM reread or backpressure.
module online_gap(
    input wire clk,rst,clear,enable,wr_en,
    input wire [12:0] wr_addr,input wire [7:0] wr_data,
    input wire [31:0] multiplier,
    output reg ready,error,output wire [255:0] features,
    output reg sum_valid,output reg [4:0] sum_channel,output reg [31:0] sum_value,
    output reg value_valid,output reg [4:0] value_channel,output reg [7:0] value_data
);
    reg [31:0] sums[0:31];
    reg [5:0] counts[0:31];
    reg [7:0] values[0:31];
    reg [31:0] completed,committed;
    reg prod_valid,round_valid;
    reg [4:0] prod_channel,round_channel;
    reg [63:0] product;
    reg [33:0] rounded;
    wire [4:0] channel=wr_addr[10:6];
    wire [31:0] final_sum=sums[channel]+{24'd0,wr_data};
    wire [7:0] quantized=(rounded>127)?8'd127:rounded[7:0];
    integer i;
    genvar c;
    generate for(c=0;c<32;c=c+1) begin: outputs
        assign features[c*8+:8]=values[c];
    end endgenerate
    always @(posedge clk) begin
        if(rst || clear) begin
            ready<=0;error<=0;completed<=0;committed<=0;
            sum_valid<=0;sum_channel<=0;sum_value<=0;
            prod_valid<=0;round_valid<=0;prod_channel<=0;round_channel<=0;product<=0;rounded<=0;
            value_valid<=0;value_channel<=0;value_data<=0;
            for(i=0;i<32;i=i+1) begin sums[i]<=0;counts[i]<=0;values[i]<=0;end
        end else begin
            sum_valid<=0;prod_valid<=sum_valid;round_valid<=prod_valid;value_valid<=round_valid;
            if(enable && wr_en) begin
                if(wr_addr>=2048 || wr_data[7] || completed[channel]) error<=1;
                else begin
                    sums[channel]<=final_sum;
                    if(counts[channel]==63) begin
                        completed[channel]<=1;
                        sum_valid<=1;sum_channel<=channel;sum_value<=final_sum;
                    end else counts[channel]<=counts[channel]+1'b1;
                end
            end
            if(sum_valid) begin
                // GAP sees nonnegative ReLU values and a positive exported multiplier.
                product<={32'd0,sum_value}*{32'd0,multiplier};prod_channel<=sum_channel;
            end
            if(prod_valid) begin
                rounded<={1'b0,product[63:31]}+product[30];round_channel<=prod_channel;
            end
            if(round_valid) begin
                values[round_channel]<=quantized;
                value_channel<=round_channel;value_data<=quantized;
                committed[round_channel]<=1;
                if((committed | (32'd1<<round_channel))==32'hffffffff) ready<=1;
            end
        end
    end
endmodule
