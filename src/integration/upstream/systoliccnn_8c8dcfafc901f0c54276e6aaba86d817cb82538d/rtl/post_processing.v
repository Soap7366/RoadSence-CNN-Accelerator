`timescale 1ns/1ps
// Each four-lane token is ONE 2x2 pool group in one output channel.
module post_processing(
    input wire clk,rst,start,input wire [6:0] tile_y,tile_x,height,width,
    input wire [5:0] oc_base,input wire [12:0] output_base,
    input wire [255:0] biases,multipliers,
    output wire rd_en,output wire [4:0] rd_addr,input wire [127:0] rd_data,
    output wire wr_en,output reg [12:0] wr_addr,output wire [7:0] wr_data,
    output reg done,
    output wire debug_valid,output wire [127:0] debug_acc,output wire [6:0] debug_index
);
    reg active,feeding;
    reg [4:0] index;
    reg [6:0] x,y;
    reg read_valid,acc_valid,prod_valid,abs_valid,round_valid,quant_valid,relu_valid,pair_valid,write_valid;
    reg [4:0] read_index,acc_index,prod_index,abs_index,round_index,quant_index,relu_index,pair_index;
    reg [12:0] read_address,acc_address,prod_address,abs_address,round_address,quant_address,relu_address,pair_address;
    reg signed [31:0] read_bias,read_mult,acc_mult;
    reg signed [31:0] acc[0:3];
    reg signed [63:0] product[0:3];
    reg [63:0] magnitude[0:3];
    reg [33:0] rounded[0:3];
    reg [3:0] abs_negative,round_negative;
    reg [7:0] q[0:3],relu_q[0:3];
    reg [7:0] pair_max0,pair_max1,pool_value;
    reg write_last;
    wire signed [31:0] selected_bias=biases[index[4:2]*32+:32];
    wire signed [31:0] selected_mult=multipliers[index[4:2]*32+:32];
    wire [12:0] oc={7'd0,oc_base}+index[4:2];
    reg [12:0] channel_offset,row_offset;
    assign rd_en=feeding && !rst;assign rd_addr=index;
    assign wr_en=write_valid && !rst;assign wr_data=pool_value;
    assign debug_valid=acc_valid;assign debug_index={acc_index,2'b00};
    genvar lane;
    generate for(lane=0;lane<4;lane=lane+1) begin: debug_lanes
        assign debug_acc[lane*32+:32]=acc[lane];
    end endgenerate
    always @* begin
        case(width)
            64:begin channel_offset=oc<<10;row_offset={7'd0,y[6:1]}<<5;end
            32:begin channel_offset=oc<<8;row_offset={7'd0,y[6:1]}<<4;end
            default:begin channel_offset=oc<<6;row_offset={7'd0,y[6:1]}<<3;end
        endcase
    end
    integer i;
    always @(posedge clk) begin
        if(rst) begin
            active<=0;feeding<=0;done<=0;index<=0;x<=0;y<=0;
            read_valid<=0;acc_valid<=0;prod_valid<=0;abs_valid<=0;round_valid<=0;
            quant_valid<=0;relu_valid<=0;pair_valid<=0;write_valid<=0;
            read_index<=0;acc_index<=0;prod_index<=0;abs_index<=0;round_index<=0;
            quant_index<=0;relu_index<=0;pair_index<=0;
            read_address<=0;acc_address<=0;prod_address<=0;abs_address<=0;
            round_address<=0;quant_address<=0;relu_address<=0;pair_address<=0;
            read_bias<=0;read_mult<=0;acc_mult<=0;abs_negative<=0;round_negative<=0;
            pair_max0<=0;pair_max1<=0;pool_value<=0;wr_addr<=0;write_last<=0;
            for(i=0;i<4;i=i+1) begin acc[i]<=0;product[i]<=0;magnitude[i]<=0;rounded[i]<=0;q[i]<=0;relu_q[i]<=0;end
        end else begin
            done<=0;
            read_valid<=rd_en;acc_valid<=read_valid;prod_valid<=acc_valid;
            abs_valid<=prod_valid;round_valid<=abs_valid;quant_valid<=round_valid;
            relu_valid<=quant_valid;pair_valid<=relu_valid;write_valid<=pair_valid;
            if(rd_en) begin
                read_index<=index;read_bias<=selected_bias;read_mult<=selected_mult;
                read_address<=output_base+channel_offset+row_offset+(x>>1)+index[1:0];
                if(index==31) feeding<=0;else index<=index+1'b1;
            end
            if(read_valid) begin
                acc_mult<=read_mult;acc_index<=read_index;acc_address<=read_address;
                for(i=0;i<4;i=i+1) acc[i]<=$signed(rd_data[i*32+:32])+read_bias;
            end
            if(acc_valid) begin
                prod_index<=acc_index;prod_address<=acc_address;
                for(i=0;i<4;i=i+1) product[i]<=acc[i]*acc_mult;
            end
            if(prod_valid) begin
                abs_index<=prod_index;abs_address<=prod_address;
                for(i=0;i<4;i=i+1) begin
                    magnitude[i]<=product[i][63]?(~product[i]+64'd1):product[i];
                    abs_negative[i]<=product[i][63];
                end
            end
            if(abs_valid) begin
                round_index<=abs_index;round_address<=abs_address;round_negative<=abs_negative;
                for(i=0;i<4;i=i+1) rounded[i]<={1'b0,magnitude[i][63:31]}+magnitude[i][30];
            end
            if(round_valid) begin
                quant_index<=round_index;quant_address<=round_address;
                for(i=0;i<4;i=i+1) begin
                    if(rounded[i]>127) q[i]<=round_negative[i]?8'h81:8'h7f;
                    else q[i]<=round_negative[i]?(~rounded[i][7:0]+8'd1):rounded[i][7:0];
                end
            end
            if(quant_valid) begin
                relu_index<=quant_index;relu_address<=quant_address;
                for(i=0;i<4;i=i+1) relu_q[i]<=q[i][7]?8'd0:q[i];
            end
            if(relu_valid) begin
                pair_max0<=relu_q[0]>relu_q[1]?relu_q[0]:relu_q[1];
                pair_max1<=relu_q[2]>relu_q[3]?relu_q[2]:relu_q[3];
                pair_index<=relu_index;pair_address<=relu_address;
            end
            if(pair_valid) begin
                pool_value<=pair_max0>pair_max1?pair_max0:pair_max1;
                wr_addr<=pair_address;write_last<=pair_index==31;
            end
            // The final Feature BRAM write commits on this edge. Only now
            // release the output bank, preserving the scheduler ownership rule.
            if(write_valid && write_last) begin active<=0;done<=1;end
            if(start && !active) begin
                active<=1;feeding<=1;index<=0;x<=tile_x;y<=tile_y;write_last<=0;
                read_valid<=0;acc_valid<=0;prod_valid<=0;abs_valid<=0;round_valid<=0;
                quant_valid<=0;relu_valid<=0;pair_valid<=0;write_valid<=0;
            end
        end
    end
endmodule
