`timescale 1ns/1ps
module weight_loader(
    input wire clk,rst,start,linear_mode,input wire [7:0] k_count,
    input wire [5:0] oc_base,input wire [12:0] weight_base,
    input wire [5:0] bias_base,quant_base,
    output wire rom_en,output reg [12:0] weight_addr,
    output wire [5:0] bias_addr,quant_addr,
    input wire [7:0] weight_data,input wire [31:0] bias_data,quant_data,
    output wire wr_en,output wire [2:0] wr_lane,output wire [7:0] wr_k,wr_data,
    output reg [255:0] biases,multipliers,output reg done
);
    localparam IDLE=0,WREQ=1,WWAIT=2,WSTORE=3,PREQ=4,PWAIT=5,PSTORE=6,WADDR=7;
    reg [2:0] state,lane;reg [7:0] k;
    assign rom_en=state==WREQ || state==PREQ;
    wire [12:0] oc={7'd0,oc_base}+lane;
    wire [12:0] oc9=(oc<<3)+oc;
    wire [12:0] offset=linear_mode?(oc<<5):((k_count==9)?oc9:((k_count==72)?(oc9<<3):(oc9<<4)));
    assign bias_addr=bias_base+oc_base+lane;
    assign quant_addr=quant_base+oc_base+lane;
    assign wr_en=state==WSTORE;assign wr_lane=lane;assign wr_k=k;assign wr_data=weight_data;
    always @(posedge clk) begin
        if(rst) begin state<=IDLE;lane<=0;k<=0;done<=0;biases<=0;multipliers<=0;weight_addr<=0;end
        else begin
            done<=0;
            case(state)
                IDLE:if(start) begin lane<=0;k<=0;state<=WADDR;end
                WADDR:begin weight_addr<=weight_base+offset+k;state<=WREQ;end
                WREQ:state<=WWAIT;
                WWAIT:state<=WSTORE;
                WSTORE:begin
                    if(k==k_count-1) begin k<=0;state<=PREQ;end
                    else begin k<=k+1'b1;state<=WADDR;end
                end
                PREQ:state<=PWAIT;
                PWAIT:state<=PSTORE;
                PSTORE:begin
                    biases[lane*32+:32]<=bias_data;multipliers[lane*32+:32]<=quant_data;
                    if(lane==(linear_mode?1:7)) begin done<=1;state<=IDLE;end
                    else begin lane<=lane+1'b1;state<=WADDR;end
                end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
