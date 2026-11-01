`timescale 1ns/1ps
module cnn_axi_accelerator #(
    parameter WEIGHT_FILE="upstream/systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d/data/dual_task_baseline2_qat/weights.hex",
    parameter BIAS_FILE="upstream/systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d/data/dual_task_baseline2_qat/bias.hex",
    parameter QUANT_FILE="upstream/systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d/data/dual_task_baseline2_qat/quant.hex"
)(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aclk, ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 50000000" *)
    input wire s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aresetn, POLARITY ACTIVE_LOW" *)
    input wire s_axi_aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 13, READ_WRITE_MODE READ_WRITE, FREQ_HZ 50000000, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1" *)
    input wire [12:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [12:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire s_axi_rready
);
    wire core_rst,core_start,core_busy,core_done,core_error,core_result_valid;
    wire signed [31:0] core_collision_int32,core_steering_int32;
    wire host_wr_en,host_rd_en,host_bank,core_host_ready,core_host_rd_valid;
    wire [12:0] host_addr;
    wire [7:0] host_wr_data,core_host_rd_data;
    cnn_axi_lite_bridge bridge (
        .s_axi_aclk(s_axi_aclk),.s_axi_aresetn(s_axi_aresetn),
        .s_axi_awaddr(s_axi_awaddr),
        .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp),
        .s_axi_bvalid(s_axi_bvalid),
        .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr),
        .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp),
        .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready),
        .core_rst(core_rst),.core_start(core_start),.core_busy(core_busy),.core_done(core_done),
        .core_error(core_error),.core_result_valid(core_result_valid),
        .core_collision_int32(core_collision_int32),.core_steering_int32(core_steering_int32),
        .host_wr_en(host_wr_en),.host_rd_en(host_rd_en),.host_bank(host_bank),
        .host_addr(host_addr),.host_wr_data(host_wr_data),
        .core_host_ready(core_host_ready),.core_host_rd_valid(core_host_rd_valid),.core_host_rd_data(core_host_rd_data));
    cnn_accelerator #(.WEIGHT_FILE(WEIGHT_FILE),.BIAS_FILE(BIAS_FILE),.QUANT_FILE(QUANT_FILE)) cnn (
        .clk(s_axi_aclk),.rst(core_rst),.start(core_start),
        .busy(core_busy),.done(core_done),.error(core_error),.result_valid(core_result_valid),
        .collision_int32(core_collision_int32),.steering_int32(core_steering_int32),
        .host_wr_en(host_wr_en),.host_rd_en(host_rd_en),.host_bank(host_bank),
        .host_addr(host_addr),.host_wr_data(host_wr_data),
        .host_ready(core_host_ready),.host_rd_valid(core_host_rd_valid),.host_rd_data(core_host_rd_data));
endmodule
