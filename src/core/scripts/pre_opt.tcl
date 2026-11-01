# Preserve the RTL read enable of the weight ROM. Automatic BRAM power gating
# inserted cooolgate logic on ENARDEN and caused a routed SDF hold violation.
# Excluding only these RAM cells keeps normal logical and physical timing checks.
set weight_rom_brams [get_cells -hier -filter {(NAME =~ weight_rom/* || NAME =~ */weight_rom/*) && REF_NAME =~ RAMB*}]
if {[llength $weight_rom_brams] == 0} {error "Weight ROM BRAM cells not found before opt_design"}
set_power_opt -exclude_cells $weight_rom_brams
puts "Preserve weight ROM enable: excluded [llength $weight_rom_brams] BRAM cells from power optimization"
