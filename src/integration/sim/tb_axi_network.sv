`timescale 1ns/1ps
module tb_axi_network;
    localparam DATA_DIR="../../upstream/systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d/data/dual_task_baseline2_qat";
    reg s_axi_aclk=0; always #10 s_axi_aclk=~s_axi_aclk;
    reg s_axi_aresetn=0;
    reg [12:0] s_axi_awaddr=0,s_axi_araddr=0;
    reg [2:0] s_axi_awprot=0,s_axi_arprot=0;
    reg s_axi_awvalid=0,s_axi_wvalid=0,s_axi_bready=0,s_axi_arvalid=0,s_axi_rready=0;
    wire s_axi_awready,s_axi_wready,s_axi_bvalid,s_axi_arready,s_axi_rvalid;
    reg [31:0] s_axi_wdata=0;
    reg [3:0] s_axi_wstrb=0;
    wire [31:0] s_axi_rdata;
    wire [1:0] s_axi_bresp,s_axi_rresp;
    cnn_axi_accelerator #(.WEIGHT_FILE({DATA_DIR,"/weights.hex"}),
        .BIAS_FILE({DATA_DIR,"/bias.hex"}),.QUANT_FILE({DATA_DIR,"/quant.hex"})) dut(.*);
    reg [7:0] pixels[0:4095];
    reg [31:0] expected_collision[0:0],expected_steering[0:0];
    reg [31:0] data,status,reset_seq,start_seq,collision,steering,result_seq,word_value,held;
    integer sample,i,k,watchdog,total_reads=0,total_writes=0;
    string filename;

    task automatic write_word(input [12:0] address,input [31:0] value,
                               input [3:0] strobes,input integer aw_delay,input integer w_delay,
                               input integer hold_cycles,input [1:0] expected_response);
        integer guard;
        reg [1:0] response;
        begin
            fork
                begin
                    repeat(aw_delay) @(negedge s_axi_aclk);
                    @(negedge s_axi_aclk);s_axi_awaddr=address;s_axi_awvalid=1;
                    guard=0;
                    do begin @(posedge s_axi_aclk);guard=guard+1;
                        if(guard>10000)$fatal(1,"AW timeout");
                    end while(!s_axi_awready);
                    @(negedge s_axi_aclk);s_axi_awvalid=0;
                end
                begin
                    repeat(w_delay) @(negedge s_axi_aclk);
                    @(negedge s_axi_aclk);s_axi_wdata=value;s_axi_wstrb=strobes;s_axi_wvalid=1;
                    do @(posedge s_axi_aclk); while(!s_axi_wready);
                    @(negedge s_axi_aclk);s_axi_wvalid=0;
                end
            join
            guard=0;
            while(!s_axi_bvalid) begin @(negedge s_axi_aclk);guard=guard+1;
                if(guard>10000)$fatal(1,"B timeout address=%h",address);end
            response=s_axi_bresp;
            repeat(hold_cycles) begin @(negedge s_axi_aclk);
                if(!s_axi_bvalid || s_axi_bresp!==response)$fatal(1,"B backpressure stability");end
            if(response!==expected_response)$fatal(1,"B response address=%h got=%h expected=%h",address,response,expected_response);
            s_axi_bready=1;@(posedge s_axi_aclk);@(negedge s_axi_aclk);s_axi_bready=0;
            total_writes=total_writes+1;
        end
    endtask

    task automatic read_word(input [12:0] address,output [31:0] value,input integer hold_cycles);
        integer guard;
        reg [31:0] saved;
        reg [1:0] response;
        begin
            @(negedge s_axi_aclk);s_axi_araddr=address;s_axi_arvalid=1;
            guard=0;
            do begin @(posedge s_axi_aclk);guard=guard+1;
                if(guard>10000)$fatal(1,"AR timeout");end while(!s_axi_arready);
            @(negedge s_axi_aclk);s_axi_arvalid=0;guard=0;
            while(!s_axi_rvalid) begin @(negedge s_axi_aclk);guard=guard+1;
                if(guard>10000)$fatal(1,"R timeout address=%h",address);end
            saved=s_axi_rdata;response=s_axi_rresp;
            repeat(hold_cycles) begin @(negedge s_axi_aclk);
                if(!s_axi_rvalid || s_axi_rdata!==saved || s_axi_rresp!==response)$fatal(1,"R backpressure stability");end
            if(response!==0)$fatal(1,"R response address=%h resp=%h",address,response);
            value=saved;s_axi_rready=1;@(posedge s_axi_aclk);@(negedge s_axi_aclk);s_axi_rready=0;
            total_reads=total_reads+1;
        end
    endtask

    initial begin
        repeat(8)@(negedge s_axi_aclk);s_axi_aresetn=1;
        repeat(8)@(negedge s_axi_aclk);
        read_word(13'h000,data,3);if(data!==32'h53434e4e)$fatal(1,"MAGIC");
        read_word(13'h004,data,0);if(data!==32'h00010000)$fatal(1,"version");
        read_word(13'h02c,data,0);if(data!==4096)$fatal(1,"input bytes");
        for(sample=0;sample<30;sample=sample+1) begin
            filename=$sformatf("%s/s%0d_conv1_input.hex",DATA_DIR,sample);$readmemh(filename,pixels);
            filename=$sformatf("%s/s%0d_collision.hex",DATA_DIR,sample);$readmemh(filename,expected_collision);
            filename=$sformatf("%s/s%0d_steering.hex",DATA_DIR,sample);$readmemh(filename,expected_steering);
            read_word(13'h018,reset_seq,0);
            write_word(13'h008,1,4'h1,sample%3,(sample+1)%3,2,0);
            read_word(13'h018,data,0);if(data!==reset_seq+1)$fatal(1,"reset sequence");
            read_word(13'h00c,status,0);
            if(status[2:0]!==3'b010 || status[4])$fatal(1,"reset status %h",status);
            for(i=0;i<4096;i=i+4) begin
                word_value={pixels[i+3],pixels[i+2],pixels[i+1],pixels[i]};
                write_word(13'h1000+i,word_value,4'hf,(i/4)%3,(i/4+1)%3,(i==4092)?3:0,0);
            end
            read_word(13'h014,data,0);if(data!==4096)$fatal(1,"complete input count");
            for(i=0;i<4096;i=i+4) begin
                word_value={pixels[i+3],pixels[i+2],pixels[i+1],pixels[i]};
                read_word(13'h1000+i,data,(i==4092)?3:0);
                if(data!==word_value)$fatal(1,"native BRAM readback sample=%0d byte=%0d got=%h exp=%h",sample,i,data,word_value);
            end
            read_word(13'h01c,start_seq,0);
            write_word(13'h008,2,4'h1,2,0,2,0);
            read_word(13'h01c,data,0);if(data!==start_seq+1)$fatal(1,"start sequence");
            read_word(13'h014,data,0);if(data!==0)$fatal(1,"start must consume frame");
            watchdog=0;status=0;
            while(!status[2]) begin
                read_word(13'h00c,status,0);watchdog=watchdog+1;
                if(status[4] || watchdog>30000)$fatal(1,"compute failure status=%h sample=%0d",status,sample);
            end
            if(status[0])$fatal(1,"valid while busy");
            read_word(13'h028,result_seq,0);if(result_seq!==start_seq+1)$fatal(1,"result sequence");
            read_word(13'h020,collision,3);read_word(13'h024,steering,3);
            if(collision!==expected_collision[0] || steering!==expected_steering[0])
                $fatal(1,"AXI network heads sample=%0d got=%0d,%0d expected=%0d,%0d",sample,$signed(collision),$signed(steering),$signed(expected_collision[0]),$signed(expected_steering[0]));
            repeat(3) begin
                read_word(13'h00c,status,1);read_word(13'h020,data,0);
                if(!status[2] || data!==collision)$fatal(1,"held result");
            end
            $display("PASS AXI network sample=%0d collision=%0d steering=%0d input_write_readback=4096",sample,$signed(collision),$signed(steering));
        end
        $display("PASS tb_axi_network: samples=30 heads=60 input_bytes=122880 reads=%0d writes=%0d",total_reads,total_writes);
        $finish;
    end
    initial begin #250000000;$fatal(1,"AXI network watchdog");end
endmodule
