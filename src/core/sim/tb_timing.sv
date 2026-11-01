`timescale 1ns/1ps
// Black-box full-layer test for the routed netlist; no RTL hierarchy assumptions.
module tb_timing;
    reg clk=0;always #10 clk=~clk;
    reg rst=1,start=0;
    reg [4:0] cfg_cin=1;reg [5:0] cfg_cout=8;
    reg [6:0] cfg_height=64,cfg_width=64;
    reg cfg_src=0,cfg_dst=1;
    reg [12:0] cfg_input_base=0,cfg_output_base=0,cfg_weight_base=0;
    reg [5:0] cfg_bias_base=0,cfg_quant_base=0;
    wire busy,done,error,host_ready,host_rd_valid;wire [7:0] host_rd_data;
    wire result_valid;wire signed [31:0] collision_int32,steering_int32;
    reg host_wr_en=0,host_rd_en=0,host_bank=0;
    reg [12:0] host_addr=0;reg [7:0] host_wr_data=0;
    cnn_layer_accelerator dut(.*);
    reg [7:0] inputs[0:4095],expected[0:8191];
    integer i,cycles=0;
    // Input delays are constrained to 0.5..2 ns after the external rising edge.
    // Drive at +1 ns, not the falling edge (+10 ns).
    task tick; begin @(posedge clk); #1; end endtask
    initial begin
        $readmemh("data/s0_conv1_input.hex",inputs);
        $readmemh("data/s0_conv1_out.hex",expected);
        // Allow the Xilinx global startup reset to finish before local reset release.
        repeat(30) tick;rst=0;
        while(host_ready!==1'b1) tick;
        for(i=0;i<4096;i=i+1) begin
            // Guard address/data for one cycle before and after the write strobe.
            tick;host_wr_en=0;host_addr=i;host_wr_data=inputs[i];
            tick;host_wr_en=1;
            tick;host_wr_en=0;
        end
        tick;host_wr_en=0;
        tick;start=1;
        tick;start=0;
        while(done!==1'b1 && cycles<500000) begin
            tick;cycles=cycles+1;
            if(cycles%100000==0) $display("TIMING progress cycles=%0d busy=%b",cycles,busy);
        end
        if(done!==1'b1 || error!==1'b0 || busy!==1'b0) $fatal(1,"timing layer timeout/error cycles=%0d",cycles);
        while(host_ready!==1'b1) tick;
        for(i=0;i<8192;i=i+1) begin
            // Compare after a full synchronous read cycle and before changing address.
            tick;host_rd_en=1;host_bank=1;host_addr=i;
            repeat(2) tick;
            // Output max delay reserves the last 2 ns of the cycle externally.
            #18;
            if(host_rd_valid!==1'b1 || host_rd_data!==expected[i])
                $fatal(1,"timing BRAM mismatch index=%0d got=%h expected=%h",i,host_rd_data,expected[i]);
        end
        host_rd_en=0;
        $display("PASS tb_timing: routed Conv1 8192 exact output bytes cycles=%0d",cycles);$finish;
    end
endmodule
