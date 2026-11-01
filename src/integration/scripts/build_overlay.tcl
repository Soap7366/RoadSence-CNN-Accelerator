# PYNQ-Z2 PS7 + native CNN AXI-Lite overlay, Vivado 2023.2.
# All generated outputs stay under this integration project.
# Usage:
# vivado -mode batch -source scripts/build_overlay.tcl -tclargs <root> check
# vivado -mode batch -source scripts/build_overlay.tcl -tclargs <root> bd
# vivado -mode batch -source scripts/build_overlay.tcl -tclargs <root> build
# check creates only a PS7 preset/IP probe; it does not synthesize or implement.
set script_root [file normalize [file join [file dirname [info script]] ..]]
set root $script_root
if {[llength $argv] > 0 && [lindex $argv 0] ne ""} {
    set root [file normalize [lindex $argv 0]]
}
set mode build
if {[llength $argv] > 1} { set mode [lindex $argv 1] }
if {$mode ni {check bd build}} { error "Mode must be check, bd, or build" }
if {[version -short] ne "2023.2"} {
    error "This project is validated for Vivado 2023.2; actual=[version -short]"
}
set part xc7z020clg400-1
if {[llength [get_parts -quiet $part]] != 1} { error "Required FPGA part is not installed: $part" }
set preset [file join $root config boards pynqz2_ps_preset.tcl]
if {![file isfile $preset]} { error "Missing local official PYNQ-Z2 PS preset: $preset" }
set upstream [file join $root upstream systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d]
set reports [file join $root reports overlay]
file mkdir $reports
source $preset
set probe [expr {$mode eq "check"}]
if {$mode eq "check"} {
    set project_name board_probe
    set project_dir [file join $root build board_probe]
} elseif {$mode eq "bd"} {
    set project_name cnn_bd_probe
    set project_dir [file join $root build bd_probe]
} else {
    set project_name cnn_overlay
    set project_dir [file join $root build overlay]
}
file mkdir $project_dir
create_project $project_name $project_dir -part $part -force
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property default_lib xil_defaultlib [current_project]
if {!$probe} {
    set rtl [glob -nocomplain -directory [file join $upstream rtl] *.v]
    foreach name {cnn_axi_lite_bridge.sv cnn_axi_accelerator.sv} {
        set path [file join $root rtl $name]
        if {![file isfile $path]} { error "Missing integration RTL: $path" }
        lappend rtl $path
    }
    if {[llength $rtl] < 15} { error "Incomplete pinned upstream RTL source set" }
    add_files -norecurse $rtl
    # Vivado 2023.2 module-reference top must be Verilog/VHDL. This wrapper
    # deliberately uses Verilog-2001 syntax; its child bridge may remain SV.
    set_property file_type Verilog [get_files [file join $root rtl cnn_axi_accelerator.sv]]
    foreach path [glob -nocomplain -directory [file join $upstream data dual_task_baseline2_qat] *.hex] {
        # Referenced by absolute parameters below; retain source tracking for reproducibility.
        if {[file tail $path] in {weights.hex bias.hex quant.hex}} { add_files -norecurse $path }
    }
    update_compile_order -fileset sources_1
}
create_bd_design cnn_system
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7]
apply_pynqz2_ps_preset $ps7
# Same physical PYNQ-Z2 DDR/MIO preset as the official source; only PL connectivity changes.
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_M_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_GP0 {0} CONFIG.PCW_USE_S_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP0 {0} CONFIG.PCW_USE_S_AXI_HP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP2 {0} CONFIG.PCW_USE_S_AXI_HP3 {0} \
    CONFIG.PCW_USE_S_AXI_ACP {0} CONFIG.PCW_USE_FABRIC_INTERRUPT {0} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_RST0_PORT {1} \
    CONFIG.PCW_EN_CLK1_PORT {0} CONFIG.PCW_EN_CLK2_PORT {0} CONFIG.PCW_EN_CLK3_PORT {0} \
    CONFIG.PCW_EN_RST1_PORT {0} CONFIG.PCW_EN_RST2_PORT {0} CONFIG.PCW_EN_RST3_PORT {0} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50}] $ps7
set probe_file [open [file join $reports board_probe.txt] w]
puts $probe_file "Vivado=[version -short]"
puts $probe_file "part=$part"
puts $probe_file "installed_pynq_board_parts=[get_board_parts -quiet *pynq*]"
puts $probe_file "preset=config/boards/pynqz2_ps_preset.tcl"
puts $probe_file "preset_source=Xilinx/PYNQ v3.0.1 boards/Pynq-Z2/petalinux_bsp/hardware_project/pynqz2.tcl"
foreach key {PCW_CRYSTAL_PERIPHERAL_FREQMHZ PCW_UIPARAM_DDR_PARTNO PCW_UIPARAM_DDR_FREQ_MHZ PCW_ENET0_ENET0_IO PCW_UART0_UART0_IO PCW_FPGA0_PERIPHERAL_FREQMHZ PCW_USE_M_AXI_GP0} {
    if {[lsearch -exact [list_property $ps7] CONFIG.$key] >= 0} {
        puts $probe_file "$key=[get_property CONFIG.$key $ps7]"
    }
}
close $probe_file
if {$probe} {
    save_bd_design
    puts "PASS PYNQZ2_BOARD_PRESET_PROBE: Vivado 2023.2, PS7 5.5, FCLK0 50 MHz; no synthesis or implementation"
    close_project
    exit
}

make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR]
make_bd_intf_pins_external [get_bd_intf_pins ps7/FIXED_IO]
set kernel_name cnn_axi_accelerator_0
set kernel [create_bd_cell -type module -reference cnn_axi_accelerator $kernel_name]
set params [list \
    CONFIG.WEIGHT_FILE [file normalize [file join $upstream data dual_task_baseline2_qat weights.hex]] \
    CONFIG.BIAS_FILE [file normalize [file join $upstream data dual_task_baseline2_qat bias.hex]] \
    CONFIG.QUANT_FILE [file normalize [file join $upstream data dual_task_baseline2_qat quant.hex]]]
set_property -dict $params $kernel

set kernel_axi [get_bd_intf_pins -of_objects $kernel -filter {MODE == Slave && VLNV =~ xilinx.com:interface:aximm_rtl:*}]
if {[llength $kernel_axi] != 1} {
    error "cnn_axi_accelerator must infer exactly one AXI memory-mapped slave interface; got=$kernel_axi"
}
set kernel_axi_name [file tail $kernel_axi]
set interconnect [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_control]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $interconnect
set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 reset_50m]
# C_EXT_RESET_HIGH is read-only: infer it from the connected PS active-low pin.
set_property CONFIG.C_AUX_RESET_HIGH {1} $reset
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one]
set_property CONFIG.CONST_VAL {1} $one
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero]
set_property CONFIG.CONST_VAL {0} $zero

connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins axi_control/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_control/M00_AXI] $kernel_axi
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_50m/ext_reset_in]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins reset_50m/dcm_locked]
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins reset_50m/aux_reset_in] [get_bd_pins reset_50m/mb_debug_sys_rst]
foreach cell [list axi_control reset_50m $kernel_name] {
    foreach pin [get_bd_pins -of_objects [get_bd_cells $cell] -filter {TYPE == clk && DIR == I}] {
        connect_bd_net [get_bd_pins ps7/FCLK_CLK0] $pin
    }
}
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins ps7/M_AXI_GP0_ACLK]
foreach cell [list axi_control $kernel_name] {
    foreach pin [get_bd_pins -of_objects [get_bd_cells $cell] -filter {TYPE == rst && DIR == I}] {
        connect_bd_net [get_bd_pins reset_50m/peripheral_aresetn] $pin
    }
}
# Let Vivado allocate the physical base. PS software must obtain it from this build's HWH.
# Module-reference segments can be named reg0; query the interface rather than
# relying on the IP-packager convention "Reg".
set slave_seg [get_bd_addr_segs -quiet -of_objects $kernel_axi]
if {[llength $slave_seg] != 1} { error "Expected one address segment on $kernel_axi; got=$slave_seg" }
puts "MODULE_REF_ADDRESS_SEGMENT=$slave_seg"
assign_bd_address -range 8K -target_address_space [get_bd_addr_spaces ps7/Data] $slave_seg
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH [get_bd_cells reset_50m]] != 0} {
    error "PS FCLK_RESET0_N must be treated as active-low"
}
save_bd_design
set config_file [open [file join $reports bd_configuration.txt] w]
puts $config_file "Vivado=[version -short]; clock_domain=FCLK0 50 MHz"
puts $config_file "kernel=$kernel_name; AXI_interface=$kernel_axi; aperture=8K"
puts $config_file "upstream_commit=8c8dcfafc901f0c54276e6aaba86d817cb82538d"
puts $config_file "model_commit=619308c77fd9af7d03bc89c494a33dda5d07386d"
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7/Data]] {
    puts $config_file "SEGMENT=$seg; OFFSET=[get_property OFFSET $seg]; RANGE=[get_property RANGE $seg]"
}
close $config_file
write_bd_tcl -force [file join $reports generated_cnn_system.tcl]
report_ip_status -file [file join $reports ip_status.rpt]
if {$mode eq "bd"} {
    puts "PASS CNN_OVERLAY_BD_PROBE: complete PS7/AXI/CNN BD validated; no synthesis or implementation"
    close_project
    exit
}

set bdfile [get_files -quiet */cnn_system.bd]
generate_target all $bdfile
set wrappers [make_wrapper -files $bdfile -top]
add_files -norecurse $wrappers
set_property top cnn_system_wrapper [current_fileset]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run -timeout 30 synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%" || ![string match "*Complete*" [get_property STATUS [get_runs synth_1]]]} {
    error "Synthesis failed: [get_property STATUS [get_runs synth_1]]"
}
launch_runs impl_1 -to_step route_design -jobs 4
wait_on_run -timeout 60 impl_1
# route_design is the deliberate stop point; a flow percentage is not an
# authoritative completion test when write_bitstream has not run yet.
if {![string match "*route_design Complete*" [get_property STATUS [get_runs impl_1]]]} {
    error "Implementation failed: [get_property STATUS [get_runs impl_1]]"
}
open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $reports timing_summary.rpt]
report_utilization -file [file join $reports utilization.rpt]
report_drc -file [file join $reports drc.rpt]
set setup_path [get_timing_paths -quiet -delay_type max -max_paths 1]
set hold_path [get_timing_paths -quiet -delay_type min -max_paths 1]
if {[llength $setup_path] == 0 || [llength $hold_path] == 0} {
    error "Cannot establish routed setup/hold timing; no bitstream exported"
}
set setup_slack [get_property SLACK $setup_path]
set hold_slack [get_property SLACK $hold_path]
if {$setup_slack < 0 || $hold_slack < 0} {
    error "Routed timing failed: setup=$setup_slack; hold=$hold_slack; no bitstream exported"
}
set artifacts [file join $root artifacts]
file mkdir $artifacts
set bitfile [file join $artifacts cnn.bit]
write_bitstream -force $bitfile
set hwh [file join $project_dir cnn_overlay.gen sources_1 bd cnn_system hw_handoff cnn_system.hwh]
if {![file isfile $hwh]} { error "Missing generated HWH paired with this BD: $hwh" }
file copy -force $hwh [file join $artifacts cnn.hwh]
# Direct write_bitstream does not set the implementation run's bitstream step.
# Keep bit/hwh as the board deliverable; export XSA without an embedded bit.
write_hw_platform -fixed -force -file [file join $artifacts cnn.xsa]
set artifact_report [open [file join $artifacts build_info.txt] w]
puts $artifact_report "Vivado=[version -short]"
puts $artifact_report "part=$part"
puts $artifact_report "clock=50 MHz"
puts $artifact_report "setup_slack_ns=$setup_slack"
puts $artifact_report "hold_slack_ns=$hold_slack"
puts $artifact_report "bit=$bitfile"
puts $artifact_report "hwh_source=$hwh"
puts $artifact_report "Physical address and interface metadata must be read from cnn.hwh."
puts $artifact_report "XSA omits the bitstream; distribute cnn.bit separately with cnn.hwh."
puts $artifact_report "This build has not programmed or verified a board."
close $artifact_report
puts "PASS CNN_OVERLAY_BUILD: paired cnn.bit/cnn.hwh exported after routed timing checks"
close_project
