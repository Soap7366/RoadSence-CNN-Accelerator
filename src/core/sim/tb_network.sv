`timescale 1ns/1ps
module tb_network #(parameter DATA_DIR="data/dual_task_baseline2_qat");
    reg clk=0;always #10 clk=~clk;
    reg rst=1,start=0,host_wr_en=0,host_rd_en=0,host_bank=0;
    reg [12:0] host_addr=0;reg [7:0] host_wr_data=0;
    wire busy,done,error,result_valid,host_ready,host_rd_valid;
    wire signed [31:0] collision_int32,steering_int32;wire [7:0] host_rd_data;
    cnn_accelerator #(.WEIGHT_FILE({DATA_DIR,"/weights.hex"}),.BIAS_FILE({DATA_DIR,"/bias.hex"}),
                      .QUANT_FILE({DATA_DIR,"/quant.hex"})) dut(.*);
    reg [7:0] inputs[0:4095],expected_pool[0:14335],expected_gap[0:31];
    reg [31:0] expected_acc[0:57343],expected_sums[0:31],expected_c[0:0],expected_s[0:0],bias_rom[0:63];
    bit seen_acc[0:57343],seen_pool[0:14335];
    bit [31:0] seen_sums,seen_gap;
    integer sample,i,j,l,h,p,oc,yy,xx,idx,offset,ci,wr,tap,wy,wx;
    integer cycles,acc_count,pool_count,sum_count,gap_count,window_count,mac_c,mac_s,linear_count;
    integer layer_cycles[0:2],network_cycles,setup_cycles,tail_cycles,layer_index;
    integer passed=0,total_acc=0,total_pool=0,total_windows=0;
    reg [7:0] window_expected;
    bit checking=0;
    string filename;
    always @(posedge clk) if(checking && !rst) begin
        if(busy) begin
            network_cycles=network_cycles+1;
            if(dut.core.layer_busy) layer_cycles[dut.core.network_layer-1]=layer_cycles[dut.core.network_layer-1]+1;
            else if(dut.core.linear_mode) tail_cycles=tail_cycles+1;
            else setup_cycles=setup_cycles+1;
        end
        if(busy && host_ready) $fatal(1,"network host ownership");
        if(dut.core.linear_wr_en && (dut.core.layer_busy || dut.core.scheduler.array_active))
            $fatal(1,"GAP feature overwrote live convolution activation bank");
        if(dut.core.linear_mode && dut.core.tile_rd && dut.core.tile_addr!=dut.core.act_rd_ic*9+dut.core.act_rd_tap)
            $fatal(1,"FC activation K mapping");
        if(dut.core.act_wr) begin
            l=dut.core.network_layer;h=(l==1)?64:((l==2)?32:16);
            for(tap=0;tap<9;tap=tap+1) for(wr=0;wr<2;wr=wr+1) begin
                wy=dut.core.prep.y+wr+tap/3-1;wx=dut.core.prep.x+dut.core.prep.position+tap%3-1;
                window_expected=0;
                if(wy>=0 && wy<h && wx>=0 && wx<h) begin
                    idx=dut.core.act_ic*h*h+wy*h+wx;
                    if(l==1) window_expected=inputs[idx];
                    else window_expected=expected_pool[((l==2)?0:8192)+idx];
                end
                if((wr==0 && dut.core.act_top[tap*8+:8]!==window_expected) ||
                   (wr==1 && dut.core.act_bottom[tap*8+:8]!==window_expected)) $fatal(1,"network window sample=%0d layer=%0d",sample,l);
                window_count=window_count+1;
            end
        end
        if(dut.core.debug_valid) begin
            l=dut.core.network_layer;h=(l==1)?64:((l==2)?32:16);offset=(l==1)?0:((l==2)?32768:49152);
            for(j=0;j<4;j=j+1) begin
                p=(dut.core.debug_index+j)%16;oc=dut.core.oc_base+dut.core.debug_index/16;
                yy=dut.core.post.y+(p%4)/2;xx=dut.core.post.x+2*(p/4)+p%2;
                idx=offset+oc*h*h+yy*h+xx;
                if(seen_acc[idx] || dut.core.debug_acc[j*32+:32]!==expected_acc[idx])
                    $fatal(1,"network acc sample=%0d layer=%0d index=%0d",sample,l,idx);
                seen_acc[idx]=1;acc_count=acc_count+1;
            end
        end
        if(dut.core.post_wr) begin
            l=dut.core.network_layer;offset=(l==1)?0:((l==2)?8192:12288);
            idx=offset+dut.core.output_addr;
            if(idx>=14336 || seen_pool[idx] || dut.core.output_data!==expected_pool[idx])
                $fatal(1,"network pool sample=%0d layer=%0d index=%0d",sample,l,idx);
            seen_pool[idx]=1;pool_count=pool_count+1;
        end
        if(dut.core.gap_sum_valid) begin
            idx=dut.core.gap_sum_channel;
            if(seen_sums[idx] || dut.core.gap_sum_value!==expected_sums[idx]) $fatal(1,"GAP sum sample=%0d channel=%0d",sample,idx);
            seen_sums[idx]=1;sum_count=sum_count+1;
        end
        if(dut.core.gap_value_valid) begin
            idx=dut.core.gap_value_channel;
            if(seen_gap[idx] || dut.core.gap_value_data!==expected_gap[idx]) $fatal(1,"GAP requant sample=%0d channel=%0d",sample,idx);
            seen_gap[idx]=1;gap_count=gap_count+1;
        end
        if(dut.core.linear_mode) begin
            if(dut.core.array.rows[0].cols[0].pe.a_valid && dut.core.array.rows[0].cols[0].pe.w_valid) mac_c=mac_c+1;
            if(dut.core.array.rows[1].cols[0].pe.a_valid && dut.core.array.rows[1].cols[0].pe.w_valid) mac_s=mac_s+1;
        end
        if(dut.core.linear_commit) begin
            if($signed(dut.core.array_results[0+:32])!==$signed(expected_c[0])-$signed(bias_rom[56]) ||
               $signed(dut.core.array_results[512+:32])!==$signed(expected_s[0])-$signed(bias_rom[57])) $fatal(1,"raw FC dot product");
            for(j=0;j<128;j=j+1) if(j!=0 && j!=16 && dut.core.array_results[j*32+:32]!==32'd0)
                $fatal(1,"inactive FC PE accumulated index=%0d",j);
            linear_count=linear_count+1;
        end
    end
    task load_expected(input integer s);
        begin
            filename=$sformatf("%s/s%0d_conv1_input.hex",DATA_DIR,s);$readmemh(filename,inputs);
            filename=$sformatf("%s/s%0d_conv1_acc.hex",DATA_DIR,s);$readmemh(filename,expected_acc,0,32767);
            filename=$sformatf("%s/s%0d_conv2_acc.hex",DATA_DIR,s);$readmemh(filename,expected_acc,32768,49151);
            filename=$sformatf("%s/s%0d_conv3_acc.hex",DATA_DIR,s);$readmemh(filename,expected_acc,49152,57343);
            filename=$sformatf("%s/s%0d_conv1_out.hex",DATA_DIR,s);$readmemh(filename,expected_pool,0,8191);
            filename=$sformatf("%s/s%0d_conv2_out.hex",DATA_DIR,s);$readmemh(filename,expected_pool,8192,12287);
            filename=$sformatf("%s/s%0d_conv3_out.hex",DATA_DIR,s);$readmemh(filename,expected_pool,12288,14335);
            filename=$sformatf("%s/s%0d_gap_sum.hex",DATA_DIR,s);$readmemh(filename,expected_sums);
            filename=$sformatf("%s/s%0d_gap.hex",DATA_DIR,s);$readmemh(filename,expected_gap);
            filename=$sformatf("%s/s%0d_collision.hex",DATA_DIR,s);$readmemh(filename,expected_c);
            filename=$sformatf("%s/s%0d_steering.hex",DATA_DIR,s);$readmemh(filename,expected_s);
        end
    endtask
    task load_image;
        begin
            while(!host_ready) @(negedge clk);
            for(i=0;i<4096;i=i+1) begin
                @(negedge clk);host_wr_en=1;host_bank=0;host_addr=i;host_wr_data=inputs[i];
            end
            @(negedge clk);host_wr_en=0;
        end
    endtask
    task launch;
        begin @(negedge clk);start=1;@(negedge clk);start=0;end
    endtask
    task reset_check;
        begin
            @(negedge clk);rst=1;host_wr_en=0;start=0;host_rd_en=0;
            repeat(4) @(negedge clk);
            if(busy || done || result_valid || dut.core.post_wr || dut.core.tile_rd || dut.core.gap_sum_valid || dut.core.gap_value_valid)
                $fatal(1,"reset did not kill network transactions");
            rst=0;repeat(4) @(negedge clk);
            if(done || result_valid) $fatal(1,"late network completion after abort");
        end
    endtask
    initial begin
        $readmemh({DATA_DIR,"/bias.hex"},bias_rom);
        repeat(5) @(negedge clk);rst=0;
        load_expected(0);
        // Abort a live Conv1 read pipeline, then the online GAP and the FC feed.
        for(integer trial=0;trial<3;trial=trial+1) begin
            load_image();launch();cycles=0;
            while(cycles<100000 && !((trial==0 && dut.core.feature_en && dut.core.prep.ret_valid) ||
                 (trial==1 && dut.core.gap_sum_valid) ||
                 (trial==2 && dut.core.linear_mode && dut.core.mapper.feeding && dut.core.tile_addr==5))) begin
                @(negedge clk);cycles=cycles+1;
            end
            if(cycles==100000) $fatal(1,"did not reach abort phase %0d",trial);
            reset_check();$display("PASS network abort phase=%0d",trial);
        end
        for(sample=0;sample<30;sample=sample+1) begin
            load_expected(sample);load_image();
            for(i=0;i<57344;i=i+1) seen_acc[i]=0;
            for(i=0;i<14336;i=i+1) seen_pool[i]=0;
            seen_sums=0;seen_gap=0;acc_count=0;pool_count=0;sum_count=0;gap_count=0;window_count=0;
            mac_c=0;mac_s=0;linear_count=0;network_cycles=0;tail_cycles=0;setup_cycles=0;
            for(i=0;i<3;i=i+1) layer_cycles[i]=0;
            checking=1;launch();cycles=0;
            while(!done && cycles<100000) begin
                @(negedge clk);cycles=cycles+1;
                // Busy starts and writes, including when the layer controller is idle.
                start=(cycles==100 || (dut.core.linear_wr_en && dut.core.linear_k==10));
                host_wr_en=(cycles==200 || (busy && !dut.core.layer_busy));
                host_bank=0;host_addr=0;host_wr_data=8'hff;
                if(result_valid && !done) $fatal(1,"result_valid before completed inference");
            end
            start=0;host_wr_en=0;
            if(!done || error || busy || !result_valid) $fatal(1,"network control failure sample=%0d",sample);
            if(collision_int32!==expected_c[0] || steering_int32!==expected_s[0]) $fatal(1,"network final output sample=%0d",sample);
            if(acc_count!=57344 || pool_count!=14336 || sum_count!=32 || gap_count!=32 ||
               window_count!=331776 || mac_c!=32 || mac_s!=32 || linear_count!=1) $fatal(1,"network coverage incomplete");
            checking=0;
            // Stable results, one done pulse, and no late writes while idle.
            repeat(5) begin
                @(negedge clk);
                if(done || busy || !result_valid || dut.core.post_wr || dut.core.gap_value_valid ||
                   collision_int32!==expected_c[0] || steering_int32!==expected_s[0]) $fatal(1,"result hold/late traffic");
            end
            while(!host_ready) @(negedge clk);
            for(i=0;i<2048;i=i+1) begin
                @(negedge clk);host_rd_en=1;host_bank=1;host_addr=i;
                repeat(2) @(posedge clk);#1;
                if(!host_rd_valid || host_rd_data!==expected_pool[12288+i]) $fatal(1,"network final BRAM readback");
            end
            @(negedge clk);host_rd_en=0;
            $display("PASS network sample=%0d collision=%0d steering=%0d acc=%0d pool=%0d sums=%0d gap=%0d windows=%0d cycles=%0d conv1=%0d conv2=%0d conv3=%0d tail=%0d setup=%0d",sample,
                collision_int32,steering_int32,acc_count,pool_count,sum_count,gap_count,window_count,network_cycles,
                layer_cycles[0],layer_cycles[1],layer_cycles[2],tail_cycles,setup_cycles);
            passed=passed+1;total_acc=total_acc+acc_count;total_pool=total_pool+pool_count;total_windows=total_windows+window_count;
        end
        $display("PASS tb_network: samples=%0d acc=%0d pool=%0d windows=%0d; 60 RTL INT32 heads; automatic chain, online GAP, shared array, reset/busy/result hold",passed,total_acc,total_pool,total_windows);$finish;
    end
    initial begin #200000000;$fatal(1,"network watchdog");end
endmodule
