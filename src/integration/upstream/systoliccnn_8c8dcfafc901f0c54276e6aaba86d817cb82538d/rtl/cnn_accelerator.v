`timescale 1ns/1ps
// Native synchronous full-network interface. One start, two stable INT32 outputs.
module cnn_accelerator #(
    parameter WEIGHT_FILE="data/dual_task_baseline2_qat/weights.hex",
    parameter BIAS_FILE="data/dual_task_baseline2_qat/bias.hex",
    parameter QUANT_FILE="data/dual_task_baseline2_qat/quant.hex"
)(
    input wire clk,rst,start,
    output wire busy,done,error,result_valid,
    output wire signed [31:0] collision_int32,steering_int32,
    input wire host_wr_en,host_rd_en,host_bank,
    input wire [12:0] host_addr,input wire [7:0] host_wr_data,
    output wire host_ready,host_rd_valid,output wire [7:0] host_rd_data
);
    cnn_layer_accelerator #(.WEIGHT_FILE(WEIGHT_FILE),.BIAS_FILE(BIAS_FILE),.QUANT_FILE(QUANT_FILE),.ENABLE_NETWORK(1)) core(
        .clk(clk),.rst(rst),.start(start),.busy(busy),.done(done),.error(error),
        .result_valid(result_valid),.collision_int32(collision_int32),.steering_int32(steering_int32),
        .cfg_cin(5'd1),.cfg_cout(6'd8),.cfg_height(7'd64),.cfg_width(7'd64),.cfg_src(1'b0),.cfg_dst(1'b1),
        .cfg_input_base(13'd0),.cfg_output_base(13'd0),.cfg_weight_base(13'd0),.cfg_bias_base(6'd0),.cfg_quant_base(6'd0),
        .host_wr_en(host_wr_en),.host_rd_en(host_rd_en),.host_bank(host_bank),.host_addr(host_addr),.host_wr_data(host_wr_data),
        .host_ready(host_ready),.host_rd_valid(host_rd_valid),.host_rd_data(host_rd_data));
endmodule
