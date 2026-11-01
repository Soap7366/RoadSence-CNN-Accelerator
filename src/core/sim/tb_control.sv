`timescale 1ns/1ps
module tb_control;
    reg clk=0;always #5 clk=~clk;
    reg rst=1,start=0,layer_done=0,layer_error=0,load_done=0,gap_ready=0,gap_error=0,array_done=0;
    wire busy,done,error,result_valid,gap_clear,gap_parameter_read,gap_parameter_store,layer_start;
    wire [1:0] layer_index;wire linear_mode,linear_load_start,linear_array_start,linear_commit,linear_wr_en,abort;
    wire [4:0] linear_k;wire [3:0] linear_ic,linear_tap;
    cnn_network_controller dut(.*);
    integer k;
    task tick;begin @(negedge clk);end endtask
    task launch;begin start=1;tick();start=0;end endtask
    initial begin
        repeat(3) tick();rst=0;launch();
        if(!busy || result_valid) $fatal(1,"controller launch");
        for(integer l=1;l<=3;l=l+1) begin
            while(!layer_start) tick();
            if(layer_index!=l) $fatal(1,"automatic layer order");
            tick();start=1;tick();start=0;
            if(layer_index!=l || !busy) $fatal(1,"busy start changed graph");
            layer_done=1;tick();layer_done=0;
        end
        repeat(5) tick();if(linear_mode || !busy) $fatal(1,"controller did not wait for GAP commit");
        gap_ready=1;
        while(!linear_load_start) tick();tick();
        load_done=1;tick();load_done=0;
        for(k=0;k<32;k=k+1) begin
            if(!linear_wr_en || linear_k!=k || linear_ic*9+linear_tap!=k) $fatal(1,"controller FC copy");
            tick();
        end
        if(!linear_array_start) $fatal(1,"controller FC launch");
        tick();repeat(40) tick();array_done=1;#1;
        if(!linear_commit) $fatal(1,"controller FC commit handshake");
        tick();array_done=0;
        if(!done || busy || error || !result_valid) $fatal(1,"controller completion");
        repeat(4) begin tick();if(done || !result_valid) $fatal(1,"controller sticky result / done pulse");end
        // Error paths must abort once, invalidate result, and permit a fresh request.
        launch();if(result_valid) $fatal(1,"new request did not invalidate old result");
        gap_error=1;tick();gap_error=0;
        if(!abort || !busy || !error) $fatal(1,"GAP error abort");
        tick();if(!done || busy || result_valid) $fatal(1,"GAP error termination");
        tick();launch();if(error) $fatal(1,"error sticky across accepted new request");
        while(!layer_start) tick();tick();layer_error=1;tick();layer_error=0;
        if(!abort || !error) $fatal(1,"layer error abort");
        tick();if(!done || result_valid || busy) $fatal(1,"layer error termination");
        rst=1;tick();rst=0;
        if(done || error || busy || result_valid) $fatal(1,"controller reset");
        $display("PASS tb_control: graph order, GAP wait, 32 FC copies, busy start, sticky result, GAP/layer error abort/restart, reset");$finish;
    end
    initial begin #100000;$fatal(1,"controller watchdog");end
endmodule
