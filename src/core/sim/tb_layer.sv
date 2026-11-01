`timescale 1ns/1ps
module tb_layer #(
    parameter DATA_DIR="data",SAMPLE_COUNT=2,CHAIN_ALL=0,RESULT_DIR=""
);
    reg clk=0;always #5 clk=~clk;
    reg rst=1,start=0;
    reg [4:0] cfg_cin;reg [5:0] cfg_cout;
    reg [6:0] cfg_height,cfg_width;
    reg cfg_src,cfg_dst;
    reg [12:0] cfg_input_base,cfg_output_base,cfg_weight_base;
    reg [5:0] cfg_bias_base,cfg_quant_base;
    wire busy,done,error,host_ready,host_rd_valid;wire [7:0] host_rd_data;
    wire result_valid;wire signed [31:0] collision_int32,steering_int32;
    reg host_wr_en=0,host_rd_en=0,host_bank=0;
    reg [12:0] host_addr=0;reg [7:0] host_wr_data=0;
    cnn_layer_accelerator #(.WEIGHT_FILE({DATA_DIR,"/weights.hex"}),
        .BIAS_FILE({DATA_DIR,"/bias.hex"}),.QUANT_FILE({DATA_DIR,"/quant.hex"})) dut(.*);
    reg [7:0] inputs[0:8191],expected_out[0:8191];
    reg [31:0] expected_acc[0:32767];
    bit seen_acc[0:32767],seen_out[0:8191];
    integer sample,layer,nin,nout,nacc,h,ci,co,ibase,obase;
    integer acc_count,out_count,cycles,overlap,prep_array_overlap,prep_post_overlap,total_triple=0,run_cycles,i;
    integer profiled_cycles,param_cycles,weight_cycles,active_prep,active_array,active_post;
    integer prep_bank_wait,array_output_wait,array_input_wait,post_input_wait;
    integer mapper_feed_cycles,prep_tiles,array_tiles,post_tiles,occ,profile_j;
    integer prep_address_slots,feature_reads,window_pair_writes;
    integer occupancy[0:7],prep_states[0:7],post_states[0:10];
    integer oc,p,yy,xx,idx,debug_lane;
    integer window_checks=0,wt,wr,wc,wy,wx,wi;
    reg [7:0] window_expected;
    integer result_file;
    bit checking=0;
    string filename;

    // Check every pre-requant output (bias included), every pool write, and uniqueness.
    always @(posedge clk) if(checking && !rst) begin
        if(busy) begin
            profiled_cycles=profiled_cycles+1;
            if(dut.controller.state!=4) param_cycles=param_cycles+1;
            if(dut.loader.state!=0) weight_cycles=weight_cycles+1;
            if(dut.scheduler.prep_active) active_prep=active_prep+1;
            if(dut.scheduler.array_active) active_array=active_array+1;
            if(dut.scheduler.post_active) active_post=active_post+1;
            if(dut.mapper.feeding) mapper_feed_cycles=mapper_feed_cycles+1;
            if(dut.prep.state==1) prep_address_slots=prep_address_slots+1;
            for(integer read_lane=0;read_lane<4;read_lane=read_lane+1)
                if(dut.feature_mask[read_lane]) feature_reads=feature_reads+1;
            if(dut.act_wr) window_pair_writes=window_pair_writes+1;
            // Check all 18 window bytes against logical CHW golden input, before MAC.
            if(dut.act_wr) begin
                for(wt=0;wt<9;wt=wt+1) begin
                    for(wr=0;wr<2;wr=wr+1) begin
                        wy=dut.prep.y+wr+wt/3-1;
                        wx=dut.prep.x+dut.prep.position+wt%3-1;
                        window_expected=0;
                        if(wy>=0 && wy<h && wx>=0 && wx<h)
                            window_expected=inputs[dut.act_ic*h*h+wy*h+wx];
                        if((wr==0 && dut.act_top[wt*8+:8]!==window_expected) ||
                           (wr==1 && dut.act_bottom[wt*8+:8]!==window_expected))
                            $fatal(1,"window mismatch sample=%0d layer=%0d ic=%0d x=%0d y=%0d tap=%0d",sample,layer,dut.act_ic,wx,wy,wt);
                        window_checks=window_checks+1;
                    end
                end
            end
            if(dut.tile_rd && dut.act_rd_ic*9+dut.act_rd_tap!=dut.tile_addr)
                $fatal(1,"activation/weight read index disagreement");
            if(dut.scheduler.prep_done) prep_tiles=prep_tiles+1;
            if(dut.scheduler.array_done) array_tiles=array_tiles+1;
            if(dut.scheduler.post_done) post_tiles=post_tiles+1;
            if(dut.scheduler.prep_active) prep_states[dut.prep.state]=prep_states[dut.prep.state]+1;
            if(dut.scheduler.post_active) begin
                if(dut.post.rd_en) post_states[0]=post_states[0]+1;
                if(dut.post.read_valid) post_states[1]=post_states[1]+1;
                if(dut.post.acc_valid) post_states[2]=post_states[2]+1;
                if(dut.post.prod_valid) post_states[3]=post_states[3]+1;
                if(dut.post.abs_valid) post_states[4]=post_states[4]+1;
                if(dut.post.round_valid) post_states[5]=post_states[5]+1;
                if(dut.post.quant_valid) post_states[6]=post_states[6]+1;
                if(dut.post.relu_valid) post_states[7]=post_states[7]+1;
                if(dut.post.wr_en) post_states[8]=post_states[8]+1;
            end
            if(dut.controller.state==4) begin
                occ=(dut.scheduler.prep_active?1:0)+(dut.scheduler.array_active?2:0)+(dut.scheduler.post_active?4:0);
                occupancy[occ]=occupancy[occ]+1;
                if(!dut.scheduler.prep_active && dut.scheduler.issued<dut.scheduler.tiles &&
                   dut.scheduler.act_full[dut.scheduler.prep_bank]) prep_bank_wait=prep_bank_wait+1;
                if(!dut.scheduler.array_active && dut.scheduler.act_full[dut.scheduler.array_bank]) begin
                    if(dut.scheduler.out_full[dut.scheduler.result_bank]) array_output_wait=array_output_wait+1;
                end else if(!dut.scheduler.array_active && dut.scheduler.issued<dut.scheduler.tiles) begin
                    array_input_wait=array_input_wait+1;
                end
                if(!dut.scheduler.post_active && !dut.scheduler.out_full[dut.scheduler.post_bank] &&
                   dut.scheduler.retired<dut.scheduler.tiles) post_input_wait=post_input_wait+1;
            end
        end
        if(dut.debug_valid) begin
          for(debug_lane=0;debug_lane<4;debug_lane=debug_lane+1) begin
            p=(dut.debug_index+debug_lane)%16;oc=dut.oc_base+dut.debug_index/16;
            yy=dut.post.y+(p%4)/2;xx=dut.post.x+2*(p/4)+(p%2);
            idx=oc*h*h+yy*h+xx;
            if(idx<0 || idx>=nacc || seen_acc[idx]) $fatal(1,"bad/duplicate accumulator address %0d",idx);
            if(dut.debug_acc[debug_lane*32+:32]!==expected_acc[idx])
                $fatal(1,"acc mismatch sample=%0d layer=%0d oc=%0d y=%0d x=%0d got=%0d expected=%0d",sample,layer,oc,yy,xx,$signed(dut.debug_acc[debug_lane*32+:32]),$signed(expected_acc[idx]));
            seen_acc[idx]=1;acc_count=acc_count+1;
          end
        end
        if(dut.post_wr) begin
            idx=dut.output_addr-obase;
            if(idx<0 || idx>=nout || seen_out[idx]) $fatal(1,"bad/duplicate pool address %0d",idx);
            if(dut.output_data!==expected_out[idx]) $fatal(1,"pool mismatch layer=%0d index=%0d got=%0d expected=%0d",layer,idx,dut.output_data,expected_out[idx]);
            seen_out[idx]=1;out_count=out_count+1;
        end
        if(dut.scheduler.prep_active && dut.scheduler.array_active && dut.scheduler.post_active) overlap=overlap+1;
        if(dut.scheduler.prep_active && dut.scheduler.array_active) prep_array_overlap=prep_array_overlap+1;
        if(dut.scheduler.prep_active && dut.scheduler.post_active) prep_post_overlap=prep_post_overlap+1;
        if(busy && host_ready) $fatal(1,"host ownership violation");
    end

    task configure(input integer l,input integer s);
        begin
            case(l)
                1:begin ci=1;co=8;h=64;cfg_weight_base=0;cfg_bias_base=0;cfg_quant_base=0;end
                2:begin ci=8;co=16;h=32;cfg_weight_base=72;cfg_bias_base=8;cfg_quant_base=8;end
                3:begin ci=16;co=32;h=16;cfg_weight_base=1224;cfg_bias_base=24;cfg_quant_base=24;end
            endcase
            cfg_cin=ci;cfg_cout=co;cfg_height=h;cfg_width=h;
            cfg_src=(l==2);if(s%2==1) cfg_src=!cfg_src;
            cfg_dst=!cfg_src;
            ibase=0;obase=0;
            if(s%2==1) begin
                if(l!=2) ibase=65;
                if(l!=1) obase=129;
                // In chain mode Conv3 must consume Conv2's actual output base.
                if(CHAIN_ALL && l==3) ibase=129;
            end
            cfg_input_base=ibase;cfg_output_base=obase;
            nin=ci*h*h;nout=co*h*h/4;nacc=co*h*h;
        end
    endtask
    task load_input;
        begin
            while(!host_ready) @(negedge clk);
            for(i=0;i<nin;i=i+1) begin
                @(negedge clk);host_wr_en=1;host_bank=cfg_src;host_addr=ibase+i;host_wr_data=inputs[i];
            end
            @(negedge clk);host_wr_en=0;
        end
    endtask
    initial begin
        configure(1,0);
        repeat(5) @(negedge clk);rst=0;
        // Invalid configuration must terminate without launching memory/array traffic.
        cfg_dst=cfg_src;start=1;
        @(negedge clk);start=0;
        if(!done || !error || busy) $fatal(1,"invalid configuration was not rejected");
        configure(1,0);
        // Abort while a tile is in flight, then reuse the same hardware without clearing RAM.
        filename={DATA_DIR,"/s0_conv1_input.hex"};$readmemh(filename,inputs,0,4095);
        load_input();@(negedge clk);start=1;@(negedge clk);start=0;
        repeat(700) @(negedge clk);rst=1;
        repeat(4) @(negedge clk);rst=0;
        if(busy) $fatal(1,"reset did not abort layer");
        // Abort with actual BRAM requests and tagged return data in flight.
        @(negedge clk);start=1;@(negedge clk);start=0;
        cycles=0;
        while(!(dut.feature_en && dut.prep.ret_valid && !dut.prep.ret_padding) && cycles<2000) begin
            @(negedge clk);cycles=cycles+1;
        end
        if(cycles==2000) $fatal(1,"did not reach a live Prep read pipeline");
        rst=1;repeat(4) @(negedge clk);
        if(busy || dut.prep.req_valid || dut.prep.ret_valid || dut.feature_en || dut.act_wr)
            $fatal(1,"reset did not drain/kill Prep transactions");
        rst=0;
        for(sample=0;sample<SAMPLE_COUNT;sample=sample+1) begin
            for(layer=1;layer<=3;layer=layer+1) begin
                configure(layer,sample);
                filename=$sformatf("%s/s%0d_conv%0d_input.hex",DATA_DIR,sample,layer);$readmemh(filename,inputs,0,nin-1);
                filename=$sformatf("%s/s%0d_conv%0d_acc.hex",DATA_DIR,sample,layer);$readmemh(filename,expected_acc,0,nacc-1);
                filename=$sformatf("%s/s%0d_conv%0d_out.hex",DATA_DIR,sample,layer);$readmemh(filename,expected_out,0,nout-1);
                // sample 0: Conv2/3 consume actual preceding RTL outputs, no input reload.
                if(layer==1 || (!CHAIN_ALL && sample!=0)) load_input();
                for(i=0;i<nacc;i=i+1) seen_acc[i]=0;
                for(i=0;i<nout;i=i+1) seen_out[i]=0;
                acc_count=0;out_count=0;overlap=0;prep_array_overlap=0;prep_post_overlap=0;cycles=0;
                profiled_cycles=0;param_cycles=0;weight_cycles=0;active_prep=0;active_array=0;active_post=0;
                prep_bank_wait=0;array_output_wait=0;array_input_wait=0;post_input_wait=0;
                mapper_feed_cycles=0;prep_tiles=0;array_tiles=0;post_tiles=0;
                prep_address_slots=0;feature_reads=0;window_pair_writes=0;
                for(profile_j=0;profile_j<8;profile_j=profile_j+1) begin occupancy[profile_j]=0;prep_states[profile_j]=0;end
                for(profile_j=0;profile_j<11;profile_j=profile_j+1) post_states[profile_j]=0;
                checking=1;
                @(negedge clk);start=1;
                @(negedge clk);start=0;
                while(!done && cycles<5000000) begin
                    @(negedge clk);cycles=cycles+1;
                    // Start and host writes during busy must not disturb the running layer.
                    if(cycles==100) begin start=1;host_wr_en=1;host_bank=cfg_src;host_addr=ibase;host_wr_data=8'hff;end
                    if(cycles==101) begin start=0;host_wr_en=0;end
                end
                if(!done || error || busy) $fatal(1,"layer timeout/error sample=%0d layer=%0d",sample,layer);
                checking=0;
                while(!host_ready) @(negedge clk);
                if(acc_count!=nacc || out_count!=nout) $fatal(1,"incomplete coverage acc=%0d/%0d out=%0d/%0d",acc_count,nacc,out_count,nout);
                if(prep_address_slots!=prep_tiles*ci*10 || window_pair_writes!=prep_tiles*ci*8)
                    $fatal(1,"incomplete parallel window/stream coverage");
                for(profile_j=0;profile_j<8;profile_j=profile_j+1)
                    if(post_states[profile_j]!=nacc/4) $fatal(1,"Post group pipeline token count mismatch stage=%0d",profile_j);
                if(post_states[8]!=nout) $fatal(1,"Post pipeline write count mismatch");
                if(prep_array_overlap==0 || prep_post_overlap==0) $fatal(1,"independent pipeline stages never overlapped");
                total_triple=total_triple+overlap;
                result_file=0;
                if(RESULT_DIR!="") begin
                    filename=$sformatf("%s/s%0d_conv%0d_out.hex",RESULT_DIR,sample,layer);
                    result_file=$fopen(filename,"w");
                    if(!result_file) $fatal(1,"cannot open actual BRAM output %s",filename);
                end
                for(i=0;i<nout;i=i+1) begin
                    @(negedge clk);host_rd_en=1;host_bank=cfg_dst;host_addr=obase+i;
                    repeat(2) @(posedge clk);#1;
                    if(!host_rd_valid || host_rd_data!==expected_out[i]) $fatal(1,"BRAM read mismatch index=%0d",i);
                    if(result_file) $fdisplay(result_file,"%02x",host_rd_data);
                end
                if(result_file) $fclose(result_file);
                @(negedge clk);host_rd_en=0;
                $display("PASS sample=%0d layer=%0d acc=%0d pool=%0d cycles=%0d triple_overlap=%0d prep_array=%0d prep_post=%0d",sample,layer,acc_count,out_count,cycles,overlap,prep_array_overlap,prep_post_overlap);
                $display("PROFILE sample=%0d layer=%0d total=%0d setup=%0d weight_loader=%0d prep_active=%0d array_active=%0d mapper_feed=%0d post_active=%0d prep_bank_wait=%0d array_output_wait=%0d array_input_wait=%0d post_input_wait=%0d prep_tiles=%0d array_tiles=%0d post_tiles=%0d",sample,layer,profiled_cycles,param_cycles,weight_cycles,active_prep,active_array,mapper_feed_cycles,active_post,prep_bank_wait,array_output_wait,array_input_wait,post_input_wait,prep_tiles,array_tiles,post_tiles);
                $display("PROFILE_STREAM sample=%0d layer=%0d address_slots=%0d actual_bram_reads=%0d window_pair_writes=%0d",sample,layer,prep_address_slots,feature_reads,window_pair_writes);
                $display("PROFILE_OCC sample=%0d layer=%0d none=%0d prep=%0d array=%0d prep_array=%0d post=%0d prep_post=%0d array_post=%0d all=%0d",sample,layer,occupancy[0],occupancy[1],occupancy[2],occupancy[3],occupancy[4],occupancy[5],occupancy[6],occupancy[7]);
                $display("PROFILE_PREP sample=%0d layer=%0d idle=%0d stream=%0d drain=%0d clear_window=%0d shift=%0d flush=%0d",sample,layer,prep_states[0],prep_states[1],prep_states[2],prep_states[3],prep_states[4],prep_states[5]);
                $display("PROFILE_POST sample=%0d layer=%0d group_width=4 read_request=%0d read_return=%0d bias=%0d multiply=%0d absolute=%0d rounded=%0d quantized=%0d relu_pool=%0d write=%0d",sample,layer,post_states[0],post_states[1],post_states[2],post_states[3],post_states[4],post_states[5],post_states[6],post_states[7],post_states[8]);
            end
        end
        if(total_triple==0) $fatal(1,"no three-stage overlap across suite");
        $display("PASS window bytes: %0d exact comparisons",window_checks);
        $display("PASS tb_layer: %0d layers; exact accumulators, pool writes and BRAM readback; reset, invalid config, busy isolation, chain",SAMPLE_COUNT*3);$finish;
    end
    initial begin #200000000; $fatal(1,"global watchdog");end
endmodule
