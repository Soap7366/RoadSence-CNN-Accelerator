set root [file normalize [file join [file dirname [info script]] ..]]
if {[llength $argv] > 0} {set core_root [lindex $argv 0]}
source [file join $root src core scripts implement_full_network.tcl]
