if {[llength $argv] != 2} {error "Usage: verify_lcd_implementation.tcl routed.dcp new_output_dir"}
set dcp [lindex $argv 0]
set out [lindex $argv 1]
if {[file exists $out]} {error "Verification output directory must be new"}
file mkdir $out
open_checkpoint $dcp
set buffers [get_cells -hier -filter {REF_NAME == OBUFT}]
if {[llength $buffers] != 24} {error "Expected 24 physical RGB tristate output buffers"}
if {[get_property PACKAGE_PIN [get_ports lcd_rst]] ne "L17"} {error "Missing LCD reset on L17"}
set pixel_period [get_property PERIOD [get_clocks lcd_pixel]]
if {abs($pixel_period - 40.0) > 0.001} {error "Expected nominal 25 MHz pixel clock"}
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$setup < 0 || $hold < 0} {error "Routed setup/hold timing failed: $setup / $hold"}
report_io -file [file join $out io.txt]
report_clocks -file [file join $out clocks.txt]
set stream [open [file join $out implementation.json] w]
puts $stream [format {{"tristate_outputs":24,"reset_pin":"L17","nominal_pixel_period_ns":%s,"setup_slack_ns":%s,"hold_slack_ns":%s,"physical_display_verified":false}} $pixel_period $setup $hold]
close $stream
puts "PASS: physical tri-state buffers, L17, clocks and routed timing; panel observation pending"
