`timescale 1ns/1ps
// AXI4-Lite to the pinned native 50 MHz CNN interface. No clock crossing.
module cnn_axi_lite_bridge #(
    parameter integer NATIVE_TIMEOUT_CYCLES = 1024,
    parameter integer COMPUTE_TIMEOUT_CYCLES = 1000000
)(
    input wire s_axi_aclk, input wire s_axi_aresetn,
    input wire [12:0] s_axi_awaddr, input wire s_axi_awvalid, output wire s_axi_awready,
    input wire [31:0] s_axi_wdata, input wire [3:0] s_axi_wstrb,
    input wire s_axi_wvalid, output wire s_axi_wready,
    output reg [1:0] s_axi_bresp, output reg s_axi_bvalid, input wire s_axi_bready,
    input wire [12:0] s_axi_araddr, input wire s_axi_arvalid, output wire s_axi_arready,
    output reg [31:0] s_axi_rdata, output reg [1:0] s_axi_rresp,
    output reg s_axi_rvalid, input wire s_axi_rready,
    output wire core_rst, output wire core_start,
    input wire core_busy, input wire core_done, input wire core_error, input wire core_result_valid,
    input wire signed [31:0] core_collision_int32, input wire signed [31:0] core_steering_int32,
    input wire core_host_ready, input wire core_host_rd_valid, input wire [7:0] core_host_rd_data,
    output wire host_wr_en, output wire host_rd_en, output wire host_bank,
    output wire [12:0] host_addr, output wire [7:0] host_wr_data
);
    localparam [1:0] OKAY=2'b00, SLVERR=2'b10;
    localparam [3:0] IDLE=0, WRITE_BYTES=1, READ_ISSUE=2, READ_WAIT=3,
                     RESET_HOLD=4, RESET_WAIT=5, START_PULSE=6;
    reg [3:0] state;
    reg aw_hold,w_hold,ar_hold;
    reg [12:0] aw_addr,ar_addr;
    reg [31:0] w_data;
    reg [3:0] w_strb;
    reg [12:0] input_count;
    reg [31:0] error_bits,reset_seq,start_seq,result_seq;
    reg [31:0] collision_word,steering_word;
    reg result_valid,running;
    reg [31:0] native_timer,compute_timer;
    reg [12:0] byte_base;
    reg [1:0] byte_lane;
    reg [31:0] transfer_word,read_word;
    reg [3:0] transfer_strb;
    reg [31:0] errors_work,command_work;
    integer lane,expected_index,window_index;
    reg write_is_valid;

    wire resetting=(state==RESET_HOLD || state==RESET_WAIT);
    wire effective_busy=core_busy || running || resetting;
    wire effective_ready=core_host_ready && !effective_busy && !result_valid && error_bits==0;
    wire native_idle=core_host_ready && !core_busy && !core_error && !core_result_valid &&
                     !running && !result_valid && error_bits==0;

    // AW and W are buffered independently. Native memory traffic is serialized.
    assign s_axi_awready=s_axi_aresetn && state==IDLE && !aw_hold && !s_axi_bvalid;
    assign s_axi_wready=s_axi_aresetn && state==IDLE && !w_hold && !s_axi_bvalid;
    assign s_axi_arready=s_axi_aresetn && state==IDLE && !ar_hold && !s_axi_rvalid;
    assign core_rst=!s_axi_aresetn || state==RESET_HOLD;
    assign core_start=s_axi_aresetn && state==START_PULSE &&
                      core_host_ready && !core_busy && !core_error && !core_result_valid;
    assign host_bank=1'b0;
    assign host_addr=byte_base+{11'd0,byte_lane};
    assign host_wr_data=(transfer_word >> (byte_lane*8));
    assign host_wr_en=s_axi_aresetn && state==WRITE_BYTES && transfer_strb[byte_lane] && native_idle;
    assign host_rd_en=s_axi_aresetn && state==READ_ISSUE && native_idle;

    task finish_write(input [1:0] response);
        begin
            s_axi_bresp<=response;s_axi_bvalid<=1'b1;state<=IDLE;
        end
    endtask
    task finish_read(input [31:0] data,input [1:0] response);
        begin
            s_axi_rdata<=data;s_axi_rresp<=response;s_axi_rvalid<=1'b1;state<=IDLE;
        end
    endtask

    always @(posedge s_axi_aclk) begin
        if(!s_axi_aresetn) begin
            state<=IDLE;aw_hold<=0;w_hold<=0;ar_hold<=0;
            aw_addr<=0;ar_addr<=0;w_data<=0;w_strb<=0;
            s_axi_bresp<=OKAY;s_axi_bvalid<=0;s_axi_rdata<=0;s_axi_rresp<=OKAY;s_axi_rvalid<=0;
            input_count<=0;error_bits<=0;reset_seq<=0;start_seq<=0;result_seq<=0;
            collision_word<=0;steering_word<=0;result_valid<=0;running<=0;
            native_timer<=0;compute_timer<=0;byte_base<=0;byte_lane<=0;
            transfer_word<=0;transfer_strb<=0;read_word<=0;
        end else begin
            errors_work=error_bits;
            if(s_axi_bvalid && s_axi_bready) s_axi_bvalid<=0;
            if(s_axi_rvalid && s_axi_rready) s_axi_rvalid<=0;
            if(s_axi_awvalid && s_axi_awready) begin aw_addr<=s_axi_awaddr;aw_hold<=1;end
            if(s_axi_wvalid && s_axi_wready) begin w_data<=s_axi_wdata;w_strb<=s_axi_wstrb;w_hold<=1;end
            if(s_axi_arvalid && s_axi_arready) begin ar_addr<=s_axi_araddr;ar_hold<=1;end

            // Only a result belonging to an accepted start can become visible.
            // START_PULSE is excluded because core consumes that pulse at this edge.
            if(!resetting && core_error) errors_work=errors_work|32'd16;
            if(running && state!=START_PULSE && !resetting) begin
                if(core_error) begin running<=0;result_valid<=0;end
                else if(core_result_valid) begin
                    if(core_busy) begin errors_work=errors_work|32'd16;running<=0;result_valid<=0;end
                    else begin
                        collision_word<=core_collision_int32;steering_word<=core_steering_int32;
                        result_seq<=start_seq;result_valid<=1;running<=0;
                    end
                end else if(compute_timer>=COMPUTE_TIMEOUT_CYCLES-1) begin
                    errors_work=errors_work|32'd32;running<=0;result_valid<=0;
                end else compute_timer<=compute_timer+1'b1;
            end

            case(state)
                IDLE: begin
                    if(aw_hold && w_hold && !s_axi_bvalid) begin
                        aw_hold<=0;w_hold<=0;native_timer<=0;
                        command_work=w_data & {{8{w_strb[3]}},{8{w_strb[2]}},
                                                   {8{w_strb[1]}},{8{w_strb[0]}}};
                        if(w_strb==0) finish_write(OKAY);
                        else if(aw_addr[1:0]!=0) finish_write(SLVERR);
                        else if(aw_addr>=13'h1000) begin
                            if(core_busy || running || result_valid || core_result_valid || errors_work!=0) begin
                                errors_work=errors_work|32'd4;finish_write(SLVERR);
                            end else begin
                                expected_index=input_count;window_index=aw_addr-13'h1000;
                                write_is_valid=1;
                                // Validate all lanes before issuing any native write.
                                for(lane=0;lane<4;lane=lane+1) if(w_strb[lane]) begin
                                    if(window_index+lane!=expected_index || expected_index>=4096 ||
                                       ((w_data >> (lane*8)) & 32'hff)>127) write_is_valid=0;
                                    expected_index=expected_index+1;
                                end
                                if(!write_is_valid) begin
                                    errors_work=errors_work|32'd1;finish_write(SLVERR);
                                end else begin
                                    byte_base<=aw_addr-13'h1000;byte_lane<=0;
                                    transfer_word<=w_data;transfer_strb<=w_strb;state<=WRITE_BYTES;
                                end
                            end
                        end else if(aw_addr==13'h008) begin
                            if(command_work==0) finish_write(OKAY);
                            else if(command_work==32'd1) begin
                                // A software reset never drops AXI requests/responses.
                                // Reset executes only after the serialized native operation has ended.
                                running<=0;result_valid<=0;input_count<=0;errors_work=0;
                                collision_word<=0;steering_word<=0;result_seq<=0;compute_timer<=0;
                                native_timer<=0;state<=RESET_HOLD;
                            end else if(command_work==32'd2) begin
                                if(core_busy || running || !core_host_ready || result_valid ||
                                   core_result_valid || errors_work!=0) begin
                                    errors_work=errors_work|32'd4;finish_write(SLVERR);
                                end else if(input_count!=4096) begin
                                    errors_work=errors_work|32'd2;finish_write(SLVERR);
                                end else begin running<=1;compute_timer<=0;state<=START_PULSE;end
                            end else begin errors_work=errors_work|32'd64;finish_write(SLVERR);end
                        end else finish_write(SLVERR);
                    end else if(ar_hold && !s_axi_rvalid) begin
                        ar_hold<=0;native_timer<=0;
                        if(ar_addr[1:0]!=0) finish_read(0,SLVERR);
                        else if(ar_addr>=13'h1000) begin
                            if(core_busy || running || result_valid || core_result_valid || errors_work!=0) begin
                                errors_work=errors_work|32'd4;finish_read(0,SLVERR);
                            end else begin
                                byte_base<=ar_addr-13'h1000;byte_lane<=0;read_word<=0;state<=READ_ISSUE;
                            end
                        end else case(ar_addr)
                            13'h000:finish_read(32'h53434e4e,OKAY);
                            13'h004:finish_read(32'h00010000,OKAY);
                            13'h008:finish_read(0,OKAY);
                            13'h00c:finish_read({27'd0,(errors_work!=0),(input_count==4096),
                                                result_valid,effective_ready,effective_busy},OKAY);
                            13'h010:finish_read(errors_work,OKAY);
                            13'h014:finish_read({19'd0,input_count},OKAY);
                            13'h018:finish_read(reset_seq,OKAY);
                            13'h01c:finish_read(start_seq,OKAY);
                            13'h020:finish_read(collision_word,OKAY);
                            13'h024:finish_read(steering_word,OKAY);
                            13'h028:finish_read(result_seq,OKAY);
                            13'h02c:finish_read(4096,OKAY);
                            13'h030:finish_read(32'h346bafa3,OKAY);
                            13'h034:finish_read(32'h1f56bd5a,OKAY);
                            13'h038:finish_read(32'h28bff0d8,OKAY);
                            13'h03c:finish_read(32'hee7d8cd8,OKAY);
                            13'h040:finish_read(32'h50e5e2da,OKAY);
                            13'h044:finish_read(32'h7f56e536,OKAY);
                            13'h048:finish_read(32'h037e46d0,OKAY);
                            13'h04c:finish_read(32'h33dc4225,OKAY);
                            13'h050:finish_read(32'h619308c7,OKAY);
                            13'h054:finish_read(32'h7fd9af7d,OKAY);
                            13'h058:finish_read(32'h03bc89c4,OKAY);
                            13'h05c:finish_read(32'h94a33dda,OKAY);
                            13'h060:finish_read(32'h5d07386d,OKAY);
                            default:finish_read(0,SLVERR);
                        endcase
                    end
                end
                WRITE_BYTES: begin
                    if(core_busy || running || result_valid || core_result_valid || errors_work!=0) begin
                        errors_work=errors_work|32'd4;finish_write(SLVERR);
                    end else if(!transfer_strb[byte_lane]) begin
                        native_timer<=0;
                        if(byte_lane==3) finish_write(OKAY);else byte_lane<=byte_lane+1'b1;
                    end else if(core_host_ready) begin
                        input_count<=input_count+1'b1;native_timer<=0;
                        if(byte_lane==3) finish_write(OKAY);else byte_lane<=byte_lane+1'b1;
                    end else if(native_timer>=NATIVE_TIMEOUT_CYCLES-1) begin
                        errors_work=errors_work|32'd8;finish_write(SLVERR);
                    end else native_timer<=native_timer+1'b1;
                end
                READ_ISSUE: begin
                    if(core_busy || running || result_valid || core_result_valid || errors_work!=0) begin
                        errors_work=errors_work|32'd4;finish_read(0,SLVERR);
                    end else if(core_host_ready) begin native_timer<=0;state<=READ_WAIT;end
                    else if(native_timer>=NATIVE_TIMEOUT_CYCLES-1) begin
                        errors_work=errors_work|32'd8;finish_read(0,SLVERR);
                    end else native_timer<=native_timer+1'b1;
                end
                READ_WAIT: begin
                    if(core_busy || running || result_valid || core_result_valid || errors_work!=0) begin
                        errors_work=errors_work|32'd4;finish_read(0,SLVERR);
                    end else if(core_host_rd_valid) begin
                        read_word[byte_lane*8+:8]<=core_host_rd_data;native_timer<=0;
                        if(byte_lane==3)
                            finish_read(read_word|({24'd0,core_host_rd_data}<<24),OKAY);
                        else begin byte_lane<=byte_lane+1'b1;state<=READ_ISSUE;end
                    end else if(native_timer>=NATIVE_TIMEOUT_CYCLES-1) begin
                        errors_work=errors_work|32'd8;finish_read(0,SLVERR);
                    end else native_timer<=native_timer+1'b1;
                end
                RESET_HOLD: begin
                    errors_work=0;
                    if(native_timer>=2) begin native_timer<=0;state<=RESET_WAIT;end
                    else native_timer<=native_timer+1'b1;
                end
                RESET_WAIT: begin
                    errors_work=0;
                    if(core_host_ready && !core_busy && !core_result_valid && !core_error) begin
                        reset_seq<=reset_seq+1'b1;finish_write(OKAY);
                    end else if(native_timer>=NATIVE_TIMEOUT_CYCLES-1) begin
                        errors_work=32'd8;finish_write(SLVERR);
                    end else native_timer<=native_timer+1'b1;
                end
                START_PULSE: begin
                    if(core_host_ready && !core_busy && !core_error && !core_result_valid && errors_work==0) begin
                        start_seq<=start_seq+1'b1;input_count<=0;compute_timer<=0;
                        finish_write(OKAY);
                    end else begin
                        running<=0;errors_work=errors_work|32'd4;finish_write(SLVERR);
                    end
                end
                default:begin errors_work=errors_work|32'd64;state<=IDLE;end
            endcase
            error_bits<=errors_work;
        end
    end
endmodule
