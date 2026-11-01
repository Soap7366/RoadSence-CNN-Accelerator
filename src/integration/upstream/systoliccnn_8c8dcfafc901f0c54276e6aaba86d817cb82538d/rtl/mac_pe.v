`timescale 1ns/1ps
// Output-stationary PE. Operands advance one cell per rising edge.
module mac_pe(
    input wire clk, rst, clear,
    input wire signed [7:0] a_in, w_in,
    input wire a_valid, w_valid, a_last, w_last,
    output reg signed [7:0] a_out, w_out,
    output reg av_out, wv_out, al_out, wl_out,
    output reg signed [31:0] psum,
    output reg done
);
    wire signed [15:0] product = a_in * w_in;
    always @(posedge clk) begin
        if (rst || clear) begin
            a_out<=0; w_out<=0; av_out<=0; wv_out<=0;
            al_out<=0; wl_out<=0; psum<=0; done<=0;
        end else begin
            a_out<=a_in; w_out<=w_in;
            av_out<=a_valid; wv_out<=w_valid;
            al_out<=a_last; wl_out<=w_last;
            done<=0;
            if (a_valid && w_valid) begin
                psum <= psum + {{16{product[15]}},product};
                done <= a_last && w_last;
            end
        end
    end
endmodule
