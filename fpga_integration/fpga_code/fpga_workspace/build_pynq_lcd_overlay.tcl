set source_dir [file dirname [file normalize [info script]]]
set build_dir [file join $::env(TEMP) fpga_lcd1_build_20261101_r2]
set out_dir [file join $source_dir overlay lcd_framebuffer_20261101]
if {[file exists $build_dir] || [file exists $out_dir]} {
    error "Use fresh build/output directories; do not overwrite prior evidence"
}
file mkdir $build_dir
set repo [file join $build_dir ip_repo]

foreach {name version files} {
    axi_lite_guard 1.1 {axi_lite_guard.sv motion_guard.sv link_watchdog.sv guard_config.svh}
    axi_lcd 1.0 {axi_lcd.sv}
} {
    create_project package_$name [file join $build_dir package_$name] -part xc7z020clg400-2
    set_property target_language Verilog [current_project]
    set_property include_dirs [list [file join $source_dir rtl]] [current_fileset]
    foreach f $files {add_files -norecurse [file join $source_dir rtl $f]}
    set_property top $name [current_fileset]
    update_compile_order -fileset sources_1
    ipx::package_project -root_dir [file join $repo $name] -vendor user.org -library user -taxonomy /UserIP -import_files
    set core [ipx::current_core]
    set_property name $name $core
    set_property version $version $core
    ipx::infer_bus_interfaces xilinx.com:interface:aximm:1.0 $core
    ipx::associate_bus_interfaces -busif s_axi -clock s_axi_aclk $core
    if {$name eq "axi_lcd"} {
        set gpio [ipx::add_bus_interface lcd_rgb $core]
        set_property bus_type_vlnv xilinx.com:interface:gpio:1.0 $gpio
        set_property abstraction_type_vlnv xilinx.com:interface:gpio_rtl:1.0 $gpio
        set_property interface_mode master $gpio
        foreach {logical physical} {TRI_I lcd_rgb_i TRI_O lcd_rgb_o TRI_T lcd_rgb_t} {
            set mapping [ipx::add_port_map $logical $gpio]
            set_property physical_name $physical $mapping
        }
        set frequency [ipx::add_bus_parameter FREQ_HZ [ipx::get_bus_interfaces lcd_clk -of_objects $core]]
        set_property value 25000000 $frequency
    }
    ipx::save_core $core
    close_project
}

create_project lcd1_guard $build_dir/project -part xc7z020clg400-2
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list $repo] [current_project]
update_ip_catalog -rebuild
create_bd_design system
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict [list CONFIG.PCW_CRYSTAL_PERIPHERAL_FREQMHZ {33.333333} \
    CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50}] [get_bd_cells ps7]
create_bd_cell -type ip -vlnv user.org:user:axi_lite_guard:1.1 guard
create_bd_cell -type ip -vlnv user.org:user:axi_lcd:1.0 lcd
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 reset_0
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_bus
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] [get_bd_cells axi_bus]
make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR]
make_bd_intf_pins_external [get_bd_intf_pins ps7/FIXED_IO]
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins axi_bus/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_bus/M00_AXI] [get_bd_intf_pins guard/s_axi]
connect_bd_intf_net [get_bd_intf_pins axi_bus/M01_AXI] [get_bd_intf_pins lcd/s_axi]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins guard/s_axi_aclk] [get_bd_pins lcd/s_axi_aclk] \
    [get_bd_pins reset_0/slowest_sync_clk] [get_bd_pins axi_bus/ACLK] \
    [get_bd_pins axi_bus/S00_ACLK] [get_bd_pins axi_bus/M00_ACLK] [get_bd_pins axi_bus/M01_ACLK]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset_0/ext_reset_in]
connect_bd_net [get_bd_pins reset_0/peripheral_aresetn] \
    [get_bd_pins guard/s_axi_aresetn] [get_bd_pins lcd/s_axi_aresetn] \
    [get_bd_pins axi_bus/S00_ARESETN] [get_bd_pins axi_bus/M00_ARESETN] [get_bd_pins axi_bus/M01_ARESETN]
connect_bd_net [get_bd_pins reset_0/interconnect_aresetn] [get_bd_pins axi_bus/ARESETN]
foreach port {lcd_clk lcd_de lcd_hs lcd_vs lcd_bl lcd_rst} {
    create_bd_port -dir O $port
    connect_bd_net [get_bd_ports $port] [get_bd_pins lcd/$port]
}
make_bd_intf_pins_external [get_bd_intf_pins lcd/lcd_rgb]
set_property name lcd_rgb [get_bd_intf_ports lcd_rgb_0]
create_bd_addr_seg -range 0x10000 -offset 0x43c00000 [get_bd_addr_spaces ps7/Data] \
    [get_bd_addr_segs guard/s_axi/reg0] SEG_guard
create_bd_addr_seg -range 0x100000 -offset 0x44000000 [get_bd_addr_spaces ps7/Data] \
    [get_bd_addr_segs lcd/s_axi/reg0] SEG_lcd
validate_bd_design
save_bd_design
set bd [get_files system.bd]
generate_target all $bd
add_files -norecurse [make_wrapper -files $bd -top]
set_property top system_wrapper [current_fileset]
add_files -fileset constrs_1 -norecurse [file join $source_dir rtl lcd_pins.xdc]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "LCD synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "LCD implementation failed"}
file mkdir $out_dir
open_run impl_1
report_utilization -file [file join $out_dir utilization.txt]
report_timing_summary -file [file join $out_dir timing_summary.txt]
report_drc -file [file join $out_dir drc.txt]
file copy [file join $build_dir project lcd1_guard.runs impl_1 system_wrapper.bit] \
    [file join $out_dir lcd1_guard.bit]
set hwh [glob [file join $build_dir project lcd1_guard.srcs sources_1 bd system hw_handoff *.hwh]]
if {[llength $hwh] != 1} {error "Expected one HWH"}
file copy [lindex $hwh 0] [file join $out_dir lcd1_guard.hwh]
source [file join $source_dir scripts adapt_pynq_hwh_clock.tcl]
adapt_pynq_hwh_clock [file join $out_dir lcd1_guard.hwh]
puts "PASS: LCD1 + B32 built; physical LCD acceptance remains pending"
