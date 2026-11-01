if {[info exists core_root]} {
    set root [file normalize $core_root]
} else {
    set root [file normalize [file join [file dirname [info script]] ..]]
}
cd $root
if {![info exists implementation_top]} {set implementation_top cnn_layer_accelerator}
if {![info exists implementation_dir]} {set implementation_dir build}
file mkdir $implementation_dir
set_param general.maxThreads 4
read_verilog [glob rtl/*.v]
synth_design -top $implementation_top -part xc7z020clg400-1
read_xdc constraints/core_50mhz.xdc
report_utilization -file $implementation_dir/utilization_synth.rpt
report_timing_summary -delay_type min_max -report_unconstrained -file $implementation_dir/timing_synth.rpt
report_timing -max_paths 20 -file $implementation_dir/paths_synth.rpt
write_checkpoint -force $implementation_dir/cnn_synth.dcp
source scripts/pre_opt.tcl
opt_design
place_design
phys_opt_design
route_design
report_utilization -file $implementation_dir/utilization_route.rpt
report_timing_summary -delay_type min_max -report_unconstrained -file $implementation_dir/timing_route.rpt
report_timing -max_paths 20 -file $implementation_dir/paths_route.rpt
report_drc -file $implementation_dir/drc_route.rpt
report_methodology -file $implementation_dir/methodology_route.rpt
write_checkpoint -force $implementation_dir/cnn_route.dcp
write_verilog -force -mode timesim -sdf_anno false $implementation_dir/cnn_timesim.v
write_sdf -force $implementation_dir/cnn_timesim.sdf
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
set f [open $implementation_dir/implementation_status.txt w]
puts $f "setup_slack_ns=[get_property SLACK $setup]"
puts $f "hold_slack_ns=[get_property SLACK $hold]"
close $f
if {[get_property SLACK $setup] < 0 || [get_property SLACK $hold] < 0} {error "Timing failed; inspect build/timing_route.rpt"}
puts "PASS implementation timing"
