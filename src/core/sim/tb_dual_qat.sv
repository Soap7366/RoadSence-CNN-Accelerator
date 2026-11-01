`timescale 1ns/1ps
// The existing DUT and checks, with all exported QAT samples actually chained.
module tb_dual_qat;
    tb_layer #(.DATA_DIR("data/dual_task_baseline2_qat"),.SAMPLE_COUNT(30),
               .CHAIN_ALL(1),.RESULT_DIR("build/dual_task_baseline2_qat/rtl_outputs")) suite();
    final $display("END tb_dual_qat; success requires PASS tb_layer: 90 layers");
endmodule
