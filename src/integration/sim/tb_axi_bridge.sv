`timescale 1ns/1ps
// Standalone protocol/native-host stub regression. This is not a CNN model.
module tb_axi_bridge;
    reg s_axi_aclk=0;always #10 s_axi_aclk=~s_axi_aclk;
    reg s_axi_aresetn=0;
    reg [12:0] s_axi_awaddr=0,s_axi_araddr=0;
    reg s_axi_awvalid=0,s_axi_wvalid=0,s_axi_arvalid=0;
    wire s_axi_awready,s_axi_wready,s_axi_arready;
    reg [31:0] s_axi_wdata=0;
    reg [3:0] s_axi_wstrb=0;
    wire [1:0] s_axi_bresp,s_axi_rresp;
    wire s_axi_bvalid,s_axi_rvalid;
    reg s_axi_bready=0,s_axi_rready=0;
    wire [31:0] s_axi_rdata;
    wire core_rst,core_start,host_wr_en,host_rd_en,host_bank;
    wire [12:0] host_addr;
    wire [7:0] host_wr_data;
    reg core_busy=0,core_done=0,core_error=0,core_result_valid=0;
    reg signed [31:0] core_collision_int32=0,core_steering_int32=0;
    wire core_host_ready;
    reg core_host_rd_valid=0;
    reg [7:0] core_host_rd_data=0;
    reg ready_reg=0,read_pending=0;
    reg [12:0] read_address=0;
    reg [7:0] native_memory[0:4095];
    reg stall_ready=0,stall_read=0;
    integer compute_mode=0,compute_left=0;
    integer native_writes=0,native_reads=0,native_starts=0;
    integer last_write_address=-1,native_reset_length=0,last_reset_length=0;
    reg reset_was_high=0;
    integer passed=0,i,j;
    reg [31:0] value,before_value,after_value;
    reg [1:0] response;
    reg [31:0] expected_word;

    assign core_host_ready=ready_reg && !stall_ready;
    cnn_axi_lite_bridge #(.NATIVE_TIMEOUT_CYCLES(12),.COMPUTE_TIMEOUT_CYCLES(48)) dut(.*);

    // Match the native core's registered host_ready and delayed host_rd_valid.
    always @(posedge s_axi_aclk) begin
        if(core_rst) begin
            core_busy<=0;core_done<=0;core_error<=0;core_result_valid<=0;
            ready_reg<=0;read_pending<=0;core_host_rd_valid<=0;core_host_rd_data<=0;
            core_collision_int32<=0;core_steering_int32<=0;compute_left<=0;
            native_writes<=0;native_reads<=0;last_write_address<=-1;
            native_reset_length<=native_reset_length+1;
            reset_was_high<=1;
        end else begin
            if(reset_was_high) begin
                last_reset_length<=native_reset_length;native_reset_length<=0;reset_was_high<=0;
            end
            ready_reg<=!core_busy && !core_start;
            core_done<=0;core_host_rd_valid<=0;
            read_pending<=host_rd_en && core_host_ready && !core_start;
            if(host_rd_en && core_host_ready && !core_start) begin
                read_address<=host_addr;native_reads<=native_reads+1;
            end
            if(read_pending && !stall_read) begin
                core_host_rd_valid<=1;core_host_rd_data<=native_memory[read_address];
            end
            if(host_wr_en && core_host_ready && !core_start) begin
                native_memory[host_addr]<=host_wr_data;
                native_writes<=native_writes+1;last_write_address<=host_addr;
            end
            if(core_start && !core_busy) begin
                native_starts<=native_starts+1;core_error<=0;core_result_valid<=0;
                if(compute_mode==1) begin
                    core_busy<=0;core_done<=1;core_result_valid<=1;
                    core_collision_int32<=32'hffff1234;core_steering_int32<=32'h80000001;
                end else begin core_busy<=1;compute_left<=5;end
            end else if(core_busy && compute_mode!=3) begin
                if(compute_left==0) begin
                    core_busy<=0;core_done<=1;
                    if(compute_mode==2) begin core_error<=1;core_result_valid<=0;end
                    else begin
                        core_result_valid<=1;
                        core_collision_int32<=32'hffff1234;core_steering_int32<=32'h80000001;
                    end
                end else compute_left<=compute_left-1;
            end
            if(host_bank!==0) $fatal(1,"bridge must select native logical bank A");
            if(host_wr_en && host_rd_en) $fatal(1,"simultaneous native read/write");
            if(core_start && (host_wr_en || host_rd_en)) $fatal(1,"start overlaps native host traffic");
            if((host_wr_en || host_rd_en) && core_busy) $fatal(1,"native host traffic while core busy");
            if((host_wr_en || host_rd_en) && host_addr>4095) $fatal(1,"native input address outside frame");
            if(host_wr_en && host_wr_data>127) $fatal(1,"invalid grayscale reached native memory");
        end
    end

    function automatic [31:0] frame_word(input integer base);
        frame_word={8'((base+3)%128),8'((base+2)%128),8'((base+1)%128),8'(base%128)};
    endfunction

    task automatic axi_write(
        input [12:0] address,input [31:0] data,input [3:0] strobes,
        input integer skew,input integer stall,input [1:0] expected_response
    );
        integer guard;
        reg [1:0] held_response;
        begin
            fork
                begin
                    if(skew==2) repeat(3) @(negedge s_axi_aclk);
                    @(negedge s_axi_aclk);s_axi_awaddr=address;s_axi_awvalid=1;
                    guard=0;
                    do begin @(posedge s_axi_aclk);guard=guard+1;
                        if(guard>10000) $fatal(1,"AW timeout");
                    end while(!s_axi_awready);
                    @(negedge s_axi_aclk);s_axi_awvalid=0;
                end
                begin
                    if(skew==1) repeat(3) @(negedge s_axi_aclk);
                    @(negedge s_axi_aclk);s_axi_wdata=data;s_axi_wstrb=strobes;s_axi_wvalid=1;
                    do @(posedge s_axi_aclk);while(!s_axi_wready);
                    @(negedge s_axi_aclk);s_axi_wvalid=0;
                end
            join
            guard=0;
            while(!s_axi_bvalid) begin
                @(negedge s_axi_aclk);guard=guard+1;
                if(guard>10000) $fatal(1,"B response timeout address=%h",address);
            end
            held_response=s_axi_bresp;
            repeat(stall) begin
                @(negedge s_axi_aclk);
                if(!s_axi_bvalid || s_axi_bresp!==held_response) $fatal(1,"B response changed under backpressure");
            end
            if(s_axi_bresp!==expected_response)
                $fatal(1,"unexpected BRESP address=%h got=%h expected=%h",address,s_axi_bresp,expected_response);
            s_axi_bready=1;
            @(posedge s_axi_aclk);
            @(negedge s_axi_aclk);s_axi_bready=0;
        end
    endtask

    task automatic axi_read(
        input [12:0] address,input integer stall,input [1:0] expected_response,output [31:0] data
    );
        integer guard;
        reg [31:0] held_data;
        reg [1:0] held_response;
        begin
            @(negedge s_axi_aclk);s_axi_araddr=address;s_axi_arvalid=1;
            guard=0;
            do begin @(posedge s_axi_aclk);guard=guard+1;
                if(guard>10000) $fatal(1,"AR timeout");
            end while(!s_axi_arready);
            @(negedge s_axi_aclk);s_axi_arvalid=0;
            guard=0;
            while(!s_axi_rvalid) begin
                @(negedge s_axi_aclk);guard=guard+1;
                if(guard>10000) $fatal(1,"R response timeout address=%h",address);
            end
            held_data=s_axi_rdata;held_response=s_axi_rresp;
            repeat(stall) begin
                @(negedge s_axi_aclk);
                if(!s_axi_rvalid || s_axi_rresp!==held_response || s_axi_rdata!==held_data)
                    $fatal(1,"R response changed under backpressure");
            end
            if(s_axi_rresp!==expected_response)
                $fatal(1,"unexpected RRESP address=%h got=%h expected=%h",address,s_axi_rresp,expected_response);
            data=s_axi_rdata;s_axi_rready=1;
            @(posedge s_axi_aclk);
            @(negedge s_axi_aclk);s_axi_rready=0;
        end
    endtask

    task automatic require_reg(input [12:0] address,input [31:0] expected);
        reg [31:0] read_value;
        begin
            axi_read(address,0,0,read_value);
            if(read_value!==expected) $fatal(1,"register %h got %h expected %h",address,read_value,expected);
        end
    endtask

    task automatic soft_reset;
        reg [31:0] previous_seq,next_seq,status_word,previous_start;
        begin
            axi_read(13'h018,0,0,previous_seq);
            axi_read(13'h01c,0,0,previous_start);
            axi_write(13'h008,1,4'hf,1,2,0);
            axi_read(13'h018,0,0,next_seq);
            if(next_seq!==previous_seq+1) $fatal(1,"reset sequence did not acknowledge completed reset");
            require_reg(13'h01c,previous_start);
            require_reg(13'h010,0);require_reg(13'h014,0);require_reg(13'h028,0);
            require_reg(13'h020,0);require_reg(13'h024,0);
            axi_read(13'h00c,0,0,status_word);
            if(status_word!==2) $fatal(1,"reset did not restore idle/ready/valid clear: %h",status_word);
            if(last_reset_length<3) $fatal(1,"native reset held for fewer than three cycles");
        end
    endtask

    task automatic load_frame;
        integer address;
        begin
            for(address=0;address<4096;address=address+4)
                axi_write(13'h1000+address,frame_word(address),4'hf,address%3,0,0);
            require_reg(13'h014,4096);
            axi_read(13'h00c,0,0,value);
            if(!value[3] || value[0] || !value[1]) $fatal(1,"full frame not marked ready");
            if(native_writes!=4096 || last_write_address!=4095) $fatal(1,"native full-frame coverage failure");
        end
    endtask

    task automatic wait_result;
        integer guard;
        reg [31:0] status_word;
        begin
            guard=0;status_word=0;
            while(!status_word[2] && guard<20) begin
                axi_read(13'h00c,1,0,status_word);guard=guard+1;
            end
            if(!status_word[2] || status_word[0] || status_word[4]) $fatal(1,"expected held valid result: %h",status_word);
        end
    endtask

    initial begin
        for(i=0;i<4096;i=i+1) native_memory[i]=0;
        repeat(5) @(negedge s_axi_aclk);
        s_axi_aresetn=1;repeat(5) @(negedge s_axi_aclk);
        require_reg(13'h000,32'h53434e4e);require_reg(13'h004,32'h00010000);
        require_reg(13'h02c,4096);
        require_reg(13'h030,32'h346bafa3);require_reg(13'h034,32'h1f56bd5a);
        require_reg(13'h038,32'h28bff0d8);require_reg(13'h03c,32'hee7d8cd8);
        require_reg(13'h040,32'h50e5e2da);require_reg(13'h044,32'h7f56e536);
        require_reg(13'h048,32'h037e46d0);require_reg(13'h04c,32'h33dc4225);
        require_reg(13'h050,32'h619308c7);require_reg(13'h054,32'h7fd9af7d);
        require_reg(13'h058,32'h03bc89c4);require_reg(13'h05c,32'h94a33dda);require_reg(13'h060,32'h5d07386d);
        axi_read(13'h070,3,2,value);axi_read(13'h001,2,2,value);
        axi_write(13'h004,99,4'hf,2,3,2);axi_write(13'h1001,1,4'hf,1,2,2);
        require_reg(13'h010,0);
        passed=passed+1;$display("PASS AXI identity/read-only/decode/alignment");

        soft_reset();
        axi_write(13'h1000,32'hffffffff,4'h0,1,5,0);
        axi_write(13'h008,3,4'h2,2,3,0);
        require_reg(13'h014,0);require_reg(13'h010,0);
        axi_write(13'h1000,32'h03020100,4'h1,1,4,0);
        require_reg(13'h014,1);
        axi_write(13'h1000,32'h03020100,4'he,2,5,0);
        require_reg(13'h014,4);
        axi_read(13'h1000,6,0,value);
        if(value!==32'h03020100) $fatal(1,"partial WSTRB lane/little-endian mismatch");
        axi_write(13'h1000,32'h03020100,4'hf,0,2,2);
        require_reg(13'h010,1);require_reg(13'h014,4);
        if(native_writes!=4) $fatal(1,"rejected duplicate write had side effects");
        passed=passed+1;$display("PASS independent AW/W, WSTRB prefix, B/R backpressure, duplicate protection");

        soft_reset();
        axi_write(13'h1004,32'h07060504,4'hf,0,0,2);
        require_reg(13'h010,1);
        if(native_writes!=0) $fatal(1,"hole write had native side effects");
        soft_reset();
        axi_write(13'h1000,32'h03028100,4'hf,1,0,2);
        require_reg(13'h010,1);require_reg(13'h014,0);
        if(native_writes!=0) $fatal(1,"invalid pixel partially committed transaction");
        soft_reset();
        axi_write(13'h008,2,4'hf,2,1,2);
        require_reg(13'h010,2);require_reg(13'h01c,0);
        soft_reset();
        axi_write(13'h008,3,4'hf,0,1,2);require_reg(13'h010,64);
        passed=passed+1;$display("PASS hole/range/incomplete-start/illegal-command rejection");

        soft_reset();load_frame();
        for(j=0;j<4096;j=j+4) begin
            axi_read(13'h1000+j,(j==4092)?5:0,0,value);
            if(value!==frame_word(j)) $fatal(1,"native full-frame readback byte=%0d",j);
        end
        if(native_reads!=4096) $fatal(1,"full-frame native read count");
        axi_read(13'h01c,0,0,before_value);
        compute_mode=0;
        axi_write(13'h008,2,4'hf,1,3,0);wait_result();
        require_reg(13'h01c,before_value+1);require_reg(13'h028,before_value+1);
        require_reg(13'h014,0);require_reg(13'h020,32'hffff1234);require_reg(13'h024,32'h80000001);
        repeat(6) begin
            axi_read(13'h020,2,0,value);
            if(value!==32'hffff1234) $fatal(1,"collision result changed while held");
        end
        axi_write(13'h008,2,4'hf,2,2,2);require_reg(13'h010,4);
        require_reg(13'h01c,before_value+1);
        passed=passed+1;$display("PASS complete 4096-byte write/read, last address, accepted start, held dual result");

        soft_reset();load_frame();compute_mode=1;
        axi_read(13'h01c,0,0,before_value);
        axi_write(13'h008,2,4'hf,2,0,0);wait_result();
        require_reg(13'h028,before_value+1);require_reg(13'h020,32'hffff1234);
        passed=passed+1;$display("PASS immediate result and generation association");

        soft_reset();
        stall_ready=1;
        axi_write(13'h1000,32'h03020100,4'hf,0,1,2);
        require_reg(13'h010,8);require_reg(13'h014,0);
        stall_ready=0;soft_reset();
        stall_read=1;
        axi_read(13'h1000,2,2,value);require_reg(13'h010,8);
        stall_read=0;soft_reset();
        passed=passed+1;$display("PASS native-ready/read timeout and reset recovery");

        load_frame();compute_mode=3;
        axi_write(13'h008,2,4'hf,0,0,0);
        axi_write(13'h1000,32'h03020100,4'hf,1,0,2);
        axi_read(13'h010,0,0,value);
        if(!(value&4)) $fatal(1,"busy input write was not rejected");
        soft_reset();
        load_frame();compute_mode=3;
        axi_write(13'h008,2,4'hf,0,0,0);
        repeat(60) @(negedge s_axi_aclk);
        require_reg(13'h010,32);soft_reset();
        passed=passed+1;$display("PASS busy traffic rejection, compute timeout, reset abort/recovery");

        load_frame();compute_mode=2;
        axi_write(13'h008,2,4'hf,0,0,0);
        repeat(12) @(negedge s_axi_aclk);
        require_reg(13'h010,16);
        axi_read(13'h00c,0,0,value);
        if(value[2]) $fatal(1,"failed core result was marked valid");
        soft_reset();
        passed=passed+1;$display("PASS core-error capture and invalid result");

        fork
            axi_read(13'h00c,15,0,value);
            begin
                repeat(3) @(negedge s_axi_aclk);
                axi_write(13'h008,1,4'hf,0,4,0);
            end
        join
        require_reg(13'h010,0);require_reg(13'h014,0);
        passed=passed+1;$display("PASS soft reset preserves outstanding AXI response");

        $display("PASS tb_axi_bridge: groups=%0d; AXI/native stub only; no CNN arithmetic",passed);
        $finish;
    end
    initial begin #10000000;$fatal(1,"tb_axi_bridge watchdog");end
endmodule
