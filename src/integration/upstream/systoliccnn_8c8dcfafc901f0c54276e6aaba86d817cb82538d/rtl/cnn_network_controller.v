`timescale 1ns/1ps
// Fixed exported graph: three Conv/Pool layers, online GAP and two shared-array FCs.
module cnn_network_controller(
    input wire clk,rst,start,
    input wire layer_done,layer_error,load_done,gap_ready,gap_error,array_done,
    output wire busy,output reg done,error,result_valid,
    output wire gap_clear,gap_parameter_read,gap_parameter_store,
    output reg [1:0] layer_index,output wire layer_start,
    output wire linear_mode,linear_load_start,linear_array_start,linear_commit,
    output wire linear_wr_en,output reg [4:0] linear_k,
    output reg [3:0] linear_ic,linear_tap,output wire abort
);
    localparam IDLE=0,GREQ=1,GWAIT=2,GSTORE=3,LSTART=4,LWAIT=5,WAIT_GAP=6,
               FC_LOAD=7,FC_WAIT=8,FC_COPY=9,FC_START=10,FC_RUN=11,ABORT=12;
    reg [3:0] state;
    assign busy=state!=IDLE;
    assign gap_clear=state==IDLE && start && !rst;
    assign gap_parameter_read=state==GREQ;
    assign gap_parameter_store=state==GSTORE;
    assign layer_start=state==LSTART;
    assign linear_mode=state>=FC_LOAD && state<=FC_RUN;
    assign linear_load_start=state==FC_LOAD;
    assign linear_wr_en=state==FC_COPY;
    assign linear_array_start=state==FC_START;
    assign linear_commit=state==FC_RUN && array_done;
    assign abort=state==ABORT;
    always @(posedge clk) begin
        if(rst) begin
            state<=IDLE;done<=0;error<=0;result_valid<=0;layer_index<=1;
            linear_k<=0;linear_ic<=0;linear_tap<=0;
        end else begin
            done<=0;
            if(state!=IDLE && state!=ABORT && (gap_error || (state==LWAIT && layer_error))) begin
                error<=1;result_valid<=0;state<=ABORT;
            end else case(state)
                IDLE:if(start) begin
                    state<=GREQ;error<=0;result_valid<=0;layer_index<=1;
                    linear_k<=0;linear_ic<=0;linear_tap<=0;
                end
                GREQ:state<=GWAIT;
                GWAIT:state<=GSTORE;
                GSTORE:state<=LSTART;
                LSTART:state<=LWAIT;
                LWAIT:if(layer_done) begin
                    if(layer_index==3) state<=WAIT_GAP;
                    else begin layer_index<=layer_index+1'b1;state<=LSTART;end
                end
                WAIT_GAP:if(gap_ready) state<=FC_LOAD;
                FC_LOAD:state<=FC_WAIT;
                FC_WAIT:if(load_done) begin
                    state<=FC_COPY;linear_k<=0;linear_ic<=0;linear_tap<=0;
                end
                FC_COPY:begin
                    if(linear_k==31) state<=FC_START;
                    else begin
                        linear_k<=linear_k+1'b1;
                        if(linear_tap==8) begin linear_tap<=0;linear_ic<=linear_ic+1'b1;end
                        else linear_tap<=linear_tap+1'b1;
                    end
                end
                FC_START:state<=FC_RUN;
                FC_RUN:if(array_done) begin state<=IDLE;done<=1;result_valid<=1;end
                ABORT:begin state<=IDLE;done<=1;end
                default:begin state<=ABORT;error<=1;result_valid<=0;end
            endcase
        end
    end
endmodule
