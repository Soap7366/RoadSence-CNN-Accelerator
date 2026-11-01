# Standalone physical verification of the native synchronous interface.
# No board pin map: do not use this file alone to generate a board bitstream.
create_clock -name core_clk -period 20.000 [get_ports clk]
set_clock_uncertainty 0.200 [get_clocks core_clk]
set data_inputs [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_input_delay -clock core_clk -max 2.000 $data_inputs
set_input_delay -clock core_clk -min 0.500 $data_inputs
set_output_delay -clock core_clk -max 2.000 [all_outputs]
set_output_delay -clock core_clk -min 0.500 [all_outputs]
set_property IOSTANDARD LVCMOS33 [get_ports *]
