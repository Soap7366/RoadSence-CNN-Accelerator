`timescale 1ns/1ps
// Independent producers/consumers with two banks at each pipeline boundary.
// A full activation bank remains owned by Array until all PE results complete.
module tile_controller(
    input wire clk,rst,start,enable,input wire [6:0] height,width,
    output wire prep_start,output reg prep_bank,output reg [6:0] prep_y,prep_x,
    input wire prep_done,
    output wire array_start,output reg array_bank,output reg result_bank,
    input wire array_done,
    output wire post_start,output reg post_bank,output wire [6:0] post_y,post_x,
    input wire post_done,output reg done
);
    reg prep_active,array_active,post_active;
    reg [1:0] act_full,out_full;
    reg [6:0] act_y[0:1],act_x[0:1],out_y[0:1],out_x[0:1];
    reg [9:0] issued,retired;
    wire [13:0] pixels=height*width;
    wire [9:0] tiles=pixels>>4;
    assign prep_start=enable && !prep_active && !act_full[prep_bank] && issued<tiles;
    assign array_start=enable && !array_active && act_full[array_bank] && !out_full[result_bank];
    assign post_start=enable && !post_active && out_full[post_bank];
    assign post_y=out_y[post_bank];assign post_x=out_x[post_bank];
    always @(posedge clk) begin
        if(rst || start) begin
            prep_active<=0;array_active<=0;post_active<=0;
            act_full<=0;out_full<=0;prep_bank<=0;array_bank<=0;result_bank<=0;post_bank<=0;
            prep_y<=0;prep_x<=0;issued<=0;retired<=0;done<=0;
            act_y[0]<=0;act_y[1]<=0;act_x[0]<=0;act_x[1]<=0;
            out_y[0]<=0;out_y[1]<=0;out_x[0]<=0;out_x[1]<=0;
        end else begin
            done<=0;
            if(prep_start) begin
                prep_active<=1;act_y[prep_bank]<=prep_y;act_x[prep_bank]<=prep_x;
                issued<=issued+1'b1;
                if(prep_x+7'd8==width) begin prep_x<=0;prep_y<=prep_y+7'd2;end
                else prep_x<=prep_x+7'd8;
            end
            if(prep_done) begin prep_active<=0;act_full[prep_bank]<=1;prep_bank<=!prep_bank;end
            if(array_start) begin
                array_active<=1;
                out_y[result_bank]<=act_y[array_bank];out_x[result_bank]<=act_x[array_bank];
            end
            if(array_done) begin
                array_active<=0;act_full[array_bank]<=0;array_bank<=!array_bank;
                out_full[result_bank]<=1;result_bank<=!result_bank;
            end
            if(post_start) post_active<=1;
            if(post_done) begin
                post_active<=0;out_full[post_bank]<=0;post_bank<=!post_bank;
                retired<=retired+1'b1;
                if(retired+1'b1==tiles) done<=1;
            end
        end
    end
endmodule
