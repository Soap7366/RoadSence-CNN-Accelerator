`timescale 1ns/1ps
// Columns are assembled directly from tagged BRAM returns.
module window_gen_4x3(
    input wire clk,rst,clear,step,input wire [31:0] column,
    output reg valid,output reg [2:0] position,
    output reg [71:0] top_window,bottom_window
);
    reg [7:0] cells[0:11];
    reg [3:0] count;
    integer r,c;
    always @(posedge clk) begin
        if(rst || clear) begin
            count<=0;valid<=0;position<=0;top_window<=0;bottom_window<=0;
            for(r=0;r<12;r=r+1) cells[r]<=0;
        end else begin
            valid<=0;
            if(step) begin
                for(r=0;r<4;r=r+1) begin
                    cells[r*3]<=cells[r*3+1];
                    cells[r*3+1]<=cells[r*3+2];
                    cells[r*3+2]<=column[r*8+:8];
                end
                count<=count+1'b1;
                if(count>=2) begin
                    valid<=1;position<=count-2;
                    for(r=0;r<3;r=r+1) begin
                        top_window[(r*3)*8+:8]<=cells[r*3+1];
                        top_window[(r*3+1)*8+:8]<=cells[r*3+2];
                        top_window[(r*3+2)*8+:8]<=column[r*8+:8];
                        bottom_window[(r*3)*8+:8]<=cells[(r+1)*3+1];
                        bottom_window[(r*3+1)*8+:8]<=cells[(r+1)*3+2];
                        bottom_window[(r*3+2)*8+:8]<=column[(r+1)*8+:8];
                    end
                end
            end
        end
    end
endmodule

module window_tile_assembler(
    input wire clk,rst,window_valid,input wire [2:0] position,
    input wire [4:0] ic,input wire [71:0] top_window,bottom_window,
    output wire ready,output wire done,
    output wire wr_en,output wire [3:0] wr_ic,
    output wire [3:0] wr_p,output wire [71:0] wr_top,wr_bottom
);
    // Nine tap banks accept the entire pair of 3x3 windows on one edge.
    // position 0..7 maps to spatial pairs 0/2,1/3,...,12/14,13/15.
    assign ready=!rst;
    assign wr_en=window_valid && !rst;
    assign done=wr_en && position==7;
    assign wr_ic=ic[3:0];
    assign wr_p={position[2:1],1'b0,position[0]};
    assign wr_top=top_window;assign wr_bottom=bottom_window;
endmodule

module prep_controller(
    input wire clk,rst,start,input wire [6:0] tile_y,tile_x,height,width,
    input wire [4:0] cin,input wire [12:0] input_base,
    output wire feature_en,output wire [3:0] feature_mask,
    output reg [51:0] feature_addr,input wire [31:0] feature_data,
    output wire act_wr_en,output wire [3:0] act_wr_ic,
    output wire [3:0] act_wr_p,output wire [71:0] act_wr_top,act_wr_bottom,
    output reg done,output wire busy
);
    localparam IDLE=0,STREAM=1,CLEAR_WINDOW=3,FLUSH=5;
    reg [2:0] state;
    reg [3:0] col;
    reg [4:0] ic;
    reg [6:0] x,y;
    // Request and return tags include padding even if no physical RAM is read.
    reg req_valid,ret_valid;
    reg [3:0] req_padding,ret_padding,req_col,ret_col;
    reg [51:0] next_addr;
    reg [3:0] next_padding;
    reg signed [9:0] iy,ix;
    reg [12:0] channel_offset,row_offset;
    integer lane;
    always @* begin
        channel_offset=0;row_offset=0;iy=0;
        ix=$signed({3'b0,x})+$signed({6'b0,col})-10'sd1;
        next_addr=0;next_padding=0;
        for(lane=0;lane<4;lane=lane+1) begin
            iy=$signed({3'b0,y})+lane-1;
            case(width)
                64:begin channel_offset={8'd0,ic}<<12;row_offset=iy<<6;end
                32:begin channel_offset={8'd0,ic}<<10;row_offset=iy<<5;end
                default:begin channel_offset={8'd0,ic}<<8;row_offset=iy<<4;end
            endcase
            next_padding[lane]=(iy<0 || ix<0 || iy>=$signed({3'b0,height}) || ix>=$signed({3'b0,width}));
            next_addr[lane*13+:13]=next_padding[lane]?13'd0:input_base+channel_offset+row_offset+ix[6:0];
        end
    end
    assign feature_mask={4{req_valid && !rst}} & ~req_padding;
    assign feature_en=|feature_mask;
    assign busy=state!=IDLE;
    wire [31:0] column;
    genvar r;
    generate for(r=0;r<4;r=r+1) begin: padding_lanes
        assign column[r*8+:8]=ret_padding[r]?8'd0:feature_data[r*8+:8];
    end endgenerate
    wire win_valid,asm_done;
    wire [2:0] position;wire [71:0] tw,bw;
    window_gen_4x3 window(.clk(clk),.rst(rst),.clear(state==CLEAR_WINDOW),
        .step(ret_valid && !rst),.column(column),.valid(win_valid),.position(position),
        .top_window(tw),.bottom_window(bw));
    window_tile_assembler assembler(.clk(clk),.rst(rst),.window_valid(win_valid),
        .position(position),.ic(ic),.top_window(tw),.bottom_window(bw),
        .ready(),.done(asm_done),.wr_en(act_wr_en),.wr_ic(act_wr_ic),
        .wr_p(act_wr_p),.wr_top(act_wr_top),.wr_bottom(act_wr_bottom));
    always @(posedge clk) begin
        if(rst) begin
            state<=IDLE;done<=0;col<=0;ic<=0;x<=0;y<=0;feature_addr<=0;
            req_valid<=0;req_padding<=0;req_col<=0;
            ret_valid<=0;ret_padding<=0;ret_col<=0;
        end else begin
            done<=0;req_valid<=0;
            ret_valid<=req_valid;ret_padding<=req_padding;ret_col<=req_col;
            case(state)
                IDLE:if(start) begin x<=tile_x;y<=tile_y;ic<=0;col<=0;state<=CLEAR_WINDOW;end
                CLEAR_WINDOW:state<=STREAM;
                STREAM:begin
                    req_valid<=1;req_padding<=next_padding;req_col<=col;feature_addr<=next_addr;
                    if(col==9) state<=FLUSH;
                    else col<=col+1'b1;
                end
                // Keep channel/bank ownership until the last window pair commits.
                FLUSH:if(asm_done) begin
                    if(ic==cin-1) begin state<=IDLE;done<=1;end
                    else begin ic<=ic+1'b1;col<=0;state<=CLEAR_WINDOW;end
                end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
