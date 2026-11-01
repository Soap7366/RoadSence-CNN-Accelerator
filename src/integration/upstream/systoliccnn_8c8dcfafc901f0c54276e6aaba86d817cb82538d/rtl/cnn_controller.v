`timescale 1ns/1ps
module cnn_controller(
    input wire clk,rst,start,
    input wire [4:0] cfg_cin,input wire [5:0] cfg_cout,
    input wire [6:0] cfg_height,cfg_width,
    input wire cfg_src,cfg_dst,
    input wire [12:0] cfg_input_base,cfg_output_base,cfg_weight_base,
    input wire [5:0] cfg_bias_base,cfg_quant_base,
    output wire busy,output reg done,error,
    output reg [4:0] cin,output reg [5:0] cout,oc_base,
    output reg [6:0] height,width,output reg src,dst,
    output reg [12:0] input_base,output_base,weight_base,
    output reg [5:0] bias_base,quant_base,output wire [7:0] k_count,
    output wire load_start,input wire load_done,
    output wire group_start,group_enable,input wire group_done
);
    localparam IDLE=0,LOAD=1,WAIT_LOAD=2,GROUP_INIT=3,RUN=4;
    reg [2:0] state;
    wire shape_ok=(cfg_cin==1 && cfg_cout==8 && cfg_height==64 && cfg_width==64) ||
                  (cfg_cin==8 && cfg_cout==16 && cfg_height==32 && cfg_width==32) ||
                  (cfg_cin==16 && cfg_cout==32 && cfg_height==16 && cfg_width==16);
    // Only the three exported shapes are accepted; use their exact sizes.
    wire [13:0] input_end={1'b0,cfg_input_base}+((cfg_cin==8)?14'd8192:14'd4096);
    wire [13:0] output_end={1'b0,cfg_output_base}+((cfg_cin==1)?14'd8192:((cfg_cin==8)?14'd4096:14'd2048));
    wire [13:0] weight_end={1'b0,cfg_weight_base}+((cfg_cin==1)?14'd72:((cfg_cin==8)?14'd1152:14'd4608));
    wire [6:0] bias_end={1'b0,cfg_bias_base}+cfg_cout;
    wire [6:0] quant_end={1'b0,cfg_quant_base}+cfg_cout;
    assign busy=state!=IDLE;assign k_count=cin*9;
    assign load_start=state==LOAD;assign group_start=state==GROUP_INIT;assign group_enable=state==RUN;
    always @(posedge clk) begin
        if(rst) begin
            state<=IDLE;done<=0;error<=0;cin<=0;cout<=0;oc_base<=0;
            height<=0;width<=0;src<=0;dst<=1;input_base<=0;output_base<=0;
            weight_base<=0;bias_base<=0;quant_base<=0;
        end else begin
            done<=0;
            case(state)
                IDLE:if(start) begin
                    error<=0;
                    if(!shape_ok || cfg_src==cfg_dst || input_end>8192 || output_end>8192 ||
                       weight_end>8192 || bias_end>64 || quant_end>64) begin error<=1;done<=1;end
                    else begin
                        cin<=cfg_cin;cout<=cfg_cout;height<=cfg_height;width<=cfg_width;
                        src<=cfg_src;dst<=cfg_dst;input_base<=cfg_input_base;output_base<=cfg_output_base;
                        weight_base<=cfg_weight_base;bias_base<=cfg_bias_base;quant_base<=cfg_quant_base;
                        oc_base<=0;state<=LOAD;
                    end
                end
                LOAD:state<=WAIT_LOAD;
                WAIT_LOAD:if(load_done) state<=GROUP_INIT;
                GROUP_INIT:state<=RUN;
                RUN:if(group_done) begin
                    if(oc_base+6'd8==cout) begin done<=1;state<=IDLE;end
                    else begin oc_base<=oc_base+6'd8;state<=LOAD;end
                end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
