`timescale 1ns/1ps
module systolic_array(
    input wire clk, rst, clear,linear_mode,
    input wire [127:0] activations, input wire [63:0] weights,
    input wire [15:0] av, al, input wire [7:0] wv, wl,
    output wire [4095:0] results,
    output wire done
);
    wire [7:0] a [0:143];
    wire [7:0] w [0:143];
    wire va[0:143], vw[0:143], la[0:143], lw[0:143];
    wire [127:0] pe_done;
    genvar r,c;
    generate
        for(c=0;c<16;c=c+1) begin: a_boundary
            assign a[c]=activations[c*8+:8];
            assign va[c]=av[c]; assign la[c]=al[c];
        end
        for(r=0;r<8;r=r+1) begin: rows
            assign w[r*17]=weights[r*8+:8];
            assign vw[r*17]=wv[r]; assign lw[r*17]=wl[r];
            for(c=0;c<16;c=c+1) begin: cols
                mac_pe pe(
                    .clk(clk),.rst(rst),.clear(clear),
                    .a_in(a[r*16+c]),.w_in(w[r*17+c]),
                    .a_valid(va[r*16+c]),.w_valid(vw[r*17+c]),
                    .a_last(la[r*16+c]),.w_last(lw[r*17+c]),
                    .a_out(a[(r+1)*16+c]),.w_out(w[r*17+c+1]),
                    .av_out(va[(r+1)*16+c]),.wv_out(vw[r*17+c+1]),
                    .al_out(la[(r+1)*16+c]),.wl_out(lw[r*17+c+1]),
                    .psum(results[(r*16+c)*32+:32]),.done(pe_done[r*16+c]));
            end
        end
    endgenerate
    assign done=linear_mode?pe_done[16]:pe_done[127];
endmodule
