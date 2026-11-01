`timescale 1ns/1ps
// Four physical row banks, total capacity 8192 INT8 values.
// Logical CHW addresses are preserved at the scalar host interface.
module feature_bram(
    input wire clk,rst,wr_en,input wire [6:0] wr_width,
    input wire [12:0] wr_addr,input wire [7:0] wr_data,
    input wire rd_en,input wire [12:0] rd_addr,output wire [7:0] rd_data,
    input wire [3:0] col_en,input wire [51:0] col_addr,
    input wire [6:0] col_width,output wire [31:0] col_data
);
    function [1:0] bank_of;
        input [12:0] addr;input [6:0] width;
        begin case(width)
            64:bank_of=addr[7:6];32:bank_of=addr[6:5];
            16:bank_of=addr[5:4];default:bank_of=addr[4:3];
        endcase end
    endfunction
    function [10:0] local_of;
        input [12:0] addr;input [6:0] width;
        begin case(width)
            64:local_of={addr[12:8],addr[5:0]};
            32:local_of={addr[12:7],addr[4:0]};
            16:local_of={addr[12:6],addr[3:0]};
            default:local_of={addr[12:5],addr[2:0]};
        endcase end
    endfunction
    // Layout follows actual writes, so host readback does not depend on cfg changes.
    // Preserve the layout across task reset, just like the RAM contents.
    reg [6:0] layout_width=64;
    reg [1:0] host_select;
    reg [1:0] column_select[0:3];
    wire [31:0] bank_data;
    integer r;
    always @(posedge clk) if(wr_en) layout_width<=wr_width;
    always @(posedge clk) begin
        if(rst) begin
            host_select<=0;
            for(r=0;r<4;r=r+1) column_select[r]<=0;
        end else begin
            if(rd_en) host_select<=bank_of(rd_addr,layout_width);
            for(r=0;r<4;r=r+1)
                if(col_en[r]) column_select[r]<=bank_of(col_addr[r*13+:13],col_width);
        end
    end
    assign rd_data=bank_data[host_select*8+:8];
    genvar b,l;
    generate for(b=0;b<4;b=b+1) begin: row_banks
        (* ram_style="block" *) reg [7:0] mem[0:2047];
        reg [7:0] q;
        reg read_enable;reg [10:0] read_address;
        integer lane;
        always @* begin
            read_enable=rd_en && bank_of(rd_addr,layout_width)==b;
            read_address=local_of(rd_addr,layout_width);
            for(lane=0;lane<4;lane=lane+1) begin
                if(col_en[lane] && bank_of(col_addr[lane*13+:13],col_width)==b) begin
                    read_enable=1;read_address=local_of(col_addr[lane*13+:13],col_width);
                end
            end
        end
        // Neither data storage nor RAM read register is reset.
        always @(posedge clk) begin
            if(wr_en && bank_of(wr_addr,wr_width)==b) mem[local_of(wr_addr,wr_width)]<=wr_data;
            if(read_enable) q<=mem[read_address];
        end
        assign bank_data[b*8+:8]=q;
    end
    for(l=0;l<4;l=l+1) begin: lanes
        assign col_data[l*8+:8]=bank_data[column_select[l]*8+:8];
    end endgenerate
endmodule

module parameter_rom #(parameter WIDTH=8,DEPTH=8192,AW=13,FILE="data/weights.hex")(
    input wire clk,en,input wire [AW-1:0] addr,output reg [WIDTH-1:0] data
);
    reg [WIDTH-1:0] mem[0:DEPTH-1];
    initial $readmemh(FILE,mem);
    always @(posedge clk) if(en) data<=mem[addr];
endmodule

module weight_tile_buffer(
    input wire clk,wr_en,input wire [2:0] wr_lane,input wire [7:0] wr_addr,
    input wire [7:0] wr_data,input wire rd_en,input wire [7:0] rd_addr,
    output wire [63:0] rd_data
);
    genvar i;
    generate for(i=0;i<8;i=i+1) begin: banks
        (* ram_style="distributed" *) reg [7:0] mem[0:143];
        reg [7:0] q;
        always @(posedge clk) begin
            if(wr_en && wr_lane==i) mem[wr_addr]<=wr_data;
            if(rd_en) q<=mem[rd_addr];
        end
        assign rd_data[i*8+:8]=q;
    end endgenerate
endmodule

module activation_tile_buffer(
    input wire clk,wr_en,wr_bank,input wire [3:0] wr_ic,
    input wire [3:0] wr_p,input wire [71:0] wr_top,wr_bottom,
    input wire rd_en,rd_bank,input wire [3:0] rd_ic,rd_tap,
    input wire linear_wr_en,input wire [3:0] linear_wr_ic,linear_wr_tap,
    input wire [7:0] linear_wr_data,
    output reg [127:0] rd_data
);
    // Bank by kernel tap AND spatial lane; the remaining depth is Cin<=16.
    // A window pair writes 18 independent banks in one cycle. Logical data
    // order is still X[k=ic*9+tap][lane], with the same one-cycle read interface.
    wire [1151:0] taps0,taps1;
    genvar tap,lane;
    generate for(tap=0;tap<9;tap=tap+1) begin: taps
        for(lane=0;lane<16;lane=lane+1) begin: lanes
            (* ram_style="distributed" *) reg [7:0] bank0[0:15];
            (* ram_style="distributed" *) reg [7:0] bank1[0:15];
            wire we=wr_en && (wr_p==lane || wr_p+4'd2==lane);
            wire [7:0] d=(wr_p==lane)?wr_top[tap*8+:8]:wr_bottom[tap*8+:8];
            always @(posedge clk) begin
                if(linear_wr_en && lane==0 && linear_wr_tap==tap) bank0[linear_wr_ic]<=linear_wr_data;
                else if(we && !wr_bank) bank0[wr_ic]<=d;
                if(we && wr_bank) bank1[wr_ic]<=d;
            end
            assign taps0[tap*128+lane*8+:8]=bank0[rd_ic];
            assign taps1[tap*128+lane*8+:8]=bank1[rd_ic];
        end
    end endgenerate
    always @(posedge clk) if(rd_en)
        rd_data<=rd_bank?taps1[rd_tap*128+:128]:taps0[rd_tap*128+:128];
endmodule

module output_tile_buffer(
    input wire clk,wr_en,wr_bank,input wire [4095:0] wr_data,
    input wire rd_en,rd_bank,input wire [4:0] rd_addr,
    output wire [127:0] rd_data
);
    // Four lanes per pool group, 32 words per lane per snapshot bank.
    genvar lane,group_index;
    generate for(lane=0;lane<4;lane=lane+1) begin: lanes
        reg [1023:0] bank0,bank1;
        reg [31:0] q;
        for(group_index=0;group_index<32;group_index=group_index+1) begin: groups
            always @(posedge clk) if(wr_en) begin
                if(wr_bank) bank1[group_index*32+:32]<=wr_data[(group_index*4+lane)*32+:32];
                else bank0[group_index*32+:32]<=wr_data[(group_index*4+lane)*32+:32];
            end
        end
        always @(posedge clk) if(rd_en)
            q<=rd_bank?bank1[rd_addr*32+:32]:bank0[rd_addr*32+:32];
        assign rd_data[lane*32+:32]=q;
    end endgenerate
endmodule
