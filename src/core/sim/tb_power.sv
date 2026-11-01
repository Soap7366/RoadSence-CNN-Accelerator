`timescale 1ns/1ps
// Representative valid inference activity; excludes host image upload from SAIF.
module tb_power;
    reg clk=0;always #10 clk=~clk;
    reg rst=1,start=0,host_wr_en=0,host_rd_en=0,host_bank=0;
    reg [12:0] host_addr=0;reg [7:0] host_wr_data=0;
    wire busy,done,error,result_valid,host_ready,host_rd_valid;
    wire signed [31:0] collision_int32,steering_int32;wire [7:0] host_rd_data;
    reg activity_window=0;
    reg [7:0] input_data[0:4095];
    reg [31:0] collision_expected[0:0],steering_expected[0:0];
    integer i,cycles=0;
    cnn_accelerator dut(.*);
    initial begin
        $readmemh("data/dual_task_baseline2_qat/s0_conv1_input.hex",input_data);
        $readmemh("data/dual_task_baseline2_qat/s0_collision.hex",collision_expected);
        $readmemh("data/dual_task_baseline2_qat/s0_steering.hex",steering_expected);
        repeat(5) @(negedge clk);rst=0;
        while(!host_ready) @(negedge clk);
        for(i=0;i<4096;i=i+1) begin
            @(negedge clk);host_wr_en=1;host_addr=i;host_wr_data=input_data[i];
        end
        @(negedge clk);host_wr_en=0;
        @(negedge clk);activity_window=1;start=1;
        @(negedge clk);start=0;
        while(!done && cycles<100000) begin @(negedge clk);cycles=cycles+1;end
        if(!done || error || !result_valid || collision_int32!==collision_expected[0] || steering_int32!==steering_expected[0])
            $fatal(1,"power stimulus inference failed");
        activity_window=0;
        $display("PASS tb_power: sample=0 cycles=%0d collision=%0d steering=%0d",cycles,collision_int32,steering_int32);
        #100000;$finish;
    end
endmodule
