set source_dir [file dirname [file dirname [file normalize [info script]]]]
set build_tag fpg1_v3_b35_20261113_revision
set dcp [file join $source_dir validation_runs fpga_build_$build_tag project lcd1_guard.runs impl_1 system_wrapper_routed.dcp]
set argv [list $dcp [file join $source_dir overlay lcd_framebuffer_$build_tag implementation_check]]
source [file join $source_dir scripts verify_lcd_implementation.tcl]
exit
