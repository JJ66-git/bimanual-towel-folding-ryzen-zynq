set script_dir [file dirname [info script]]
set build_dir [file join $::env(TEMP) codex_own_hybrid_guard_watchdog_build2]
set rtl_dir [file join $build_dir rtl]
file mkdir $rtl_dir
foreach source_file {axi_lite_guard.sv motion_guard.sv link_watchdog.sv guard_config.svh} {
    file copy -force [file join $script_dir rtl $source_file] $rtl_dir
}

create_project own_hybrid_motion_guard_watchdog $build_dir -part xc7z020clg400-2 -force
set_property target_language Verilog [current_project]
add_files -norecurse [list \
    [file join $rtl_dir axi_lite_guard.sv] \
    [file join $rtl_dir motion_guard.sv] \
    [file join $rtl_dir link_watchdog.sv]]
set_property top axi_lite_guard [current_fileset]
update_compile_order -fileset sources_1

set ip_repo_dir [file join $build_dir ip_repo]
set ip_core_dir [file join $ip_repo_dir axi_lite_guard_1_1]
ipx::package_project -root_dir $ip_core_dir -vendor user.org -library user -taxonomy /UserIP -import_files
set core [ipx::current_core]
set_property name axi_lite_guard $core
set_property version 1.1 $core
set_property display_name {Own Hybrid Motion Guard with Monitor Watchdog} $core
ipx::infer_bus_interfaces xilinx.com:interface:aximm:1.0 $core
ipx::associate_bus_interfaces -busif s_axi -clock s_axi_aclk $core
ipx::save_core $core
set_property ip_repo_paths [list $ip_repo_dir] [current_project]
update_ip_catalog -rebuild

create_bd_design system
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict [list \
    CONFIG.PCW_CRYSTAL_PERIPHERAL_FREQMHZ {33.333333} \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50}] [get_bd_cells ps7]
create_bd_cell -type ip -vlnv user.org:user:axi_lite_guard:1.1 guard
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 reset_0
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_bus
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells axi_bus]

make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR]
make_bd_intf_pins_external [get_bd_intf_pins ps7/FIXED_IO]
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins axi_bus/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_bus/M00_AXI] [get_bd_intf_pins guard/s_axi]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins guard/s_axi_aclk] [get_bd_pins reset_0/slowest_sync_clk] \
    [get_bd_pins axi_bus/ACLK] [get_bd_pins axi_bus/S00_ACLK] [get_bd_pins axi_bus/M00_ACLK]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_0/ext_reset_in]
connect_bd_net [get_bd_pins reset_0/peripheral_aresetn] [get_bd_pins guard/s_axi_aresetn]
connect_bd_net [get_bd_pins reset_0/interconnect_aresetn] [get_bd_pins axi_bus/ARESETN]
connect_bd_net [get_bd_pins reset_0/peripheral_aresetn] \
    [get_bd_pins axi_bus/S00_ARESETN] [get_bd_pins axi_bus/M00_ARESETN]
assign_bd_address
validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
generate_target all $bd_file
set wrapper_files [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper_files
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1

set out_dir [file join $script_dir overlay]
set overlay_stem own_hybrid_motion_guard_watchdog
file mkdir $out_dir
set synth_ip_list [concat \
    [get_ips system_ps7_0] \
    [get_ips system_guard_0] \
    [get_ips system_reset_0_0] \
    [get_ips system_auto_pc_0]]
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "Synthesis failed: [get_property STATUS [get_runs synth_1]]"
}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
set bit_file [file join $build_dir own_hybrid_motion_guard_watchdog.runs impl_1 system_wrapper.bit]
if {![file exists $bit_file]} {
    error "Bitstream missing: [get_property STATUS [get_runs impl_1]]"
}
file copy -force $bit_file [file join $out_dir ${overlay_stem}.bit]
open_run impl_1
report_utilization -file [file join $out_dir watchdog_utilization.txt]
report_timing_summary -file [file join $out_dir watchdog_timing_summary.txt]
set hwh_files [glob -nocomplain [file join $build_dir own_hybrid_motion_guard_watchdog.srcs sources_1 bd system hw_handoff *.hwh]]
if {[llength $hwh_files] != 1} {
    error "Expected one HWH in hw_handoff, got: $hwh_files"
}
file copy -force [lindex $hwh_files 0] [file join $out_dir ${overlay_stem}.hwh]
source [file join $script_dir scripts adapt_pynq_hwh_clock.tcl]
adapt_pynq_hwh_clock [file join $out_dir ${overlay_stem}.hwh]
puts "PASS: built [file join $out_dir ${overlay_stem}.bit] and .hwh"
