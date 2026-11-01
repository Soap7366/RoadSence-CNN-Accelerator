# Finalize an already routed CNN overlay without synthesis or implementation.
# Usage: vivado -mode batch -notrace -source scripts/finish_overlay.tcl -tclargs <root>
set root [file normalize [file join [file dirname [info script]] ..]]
if {[llength $argv] > 0 && [lindex $argv 0] ne ""} { set root [file normalize [lindex $argv 0]] }
if {[version -short] ne "2023.2"} { error "Expected Vivado 2023.2" }
set part xc7z020clg400-1
set project_dir [file join $root build overlay]
set xpr [file join $project_dir cnn_overlay.xpr]
if {![file isfile $xpr]} { error "Missing existing routed project: $xpr" }
open_project $xpr
if {[get_property PART [current_project]] ne $part} { error "Unexpected project part" }
set run_status [get_property STATUS [get_runs impl_1]]
if {![string match "*route_design Complete*" $run_status] && ![string match "*write_bitstream Complete*" $run_status]} {
    error "Existing impl_1 must already be routed: status=$run_status"
}
open_run impl_1
set reports [file join $root reports overlay]
file mkdir $reports
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $reports finish_timing_summary.rpt]
report_utilization -file [file join $reports finish_utilization.rpt]
report_drc -file [file join $reports finish_drc.rpt]
set setup_path [get_timing_paths -quiet -delay_type max -max_paths 1]
set hold_path [get_timing_paths -quiet -delay_type min -max_paths 1]
if {[llength $setup_path] == 0 || [llength $hold_path] == 0} {
    error "Missing setup/hold paths; no artifact finalization"
}
set setup_slack [get_property SLACK $setup_path]
set hold_slack [get_property SLACK $hold_path]
if {$setup_slack < 0 || $hold_slack < 0} {
    error "Routed timing failed: setup=$setup_slack hold=$hold_slack"
}
set artifacts [file join $root artifacts]
file mkdir $artifacts
set bitfile [file join $artifacts cnn.bit]
# This operates on the opened routed DCP, without launch_runs/reset_run.
write_bitstream -force $bitfile
set hwh [file join $project_dir cnn_overlay.gen sources_1 bd cnn_system hw_handoff cnn_system.hwh]
if {![file isfile $hwh]} { error "Missing HWH from this routed project's BD" }
file copy -force $hwh [file join $artifacts cnn.hwh]
# Direct write_bitstream does not mark impl_1's bitstream step complete; export
# the hardware platform without include_bit and distribute cnn.bit separately.
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
puts "PASS CNN_OVERLAY_FINISH: existing routed DCP, setup=$setup_slack ns hold=$hold_slack ns, paired cnn.bit/cnn.hwh and bit-free XSA exported"
close_project
