proc adapt_pynq_hwh_clock {hwh_file} {
    # Match the existing PYNQ boot IOPLL (33.333333 MHz * 30), without changing PLLs.
    set stream [open $hwh_file r]
    fconfigure $stream -encoding utf-8 -translation auto
    set data [read $stream]
    close $stream
    foreach {name value} {
        PCW_FCLK0_PERIPHERAL_DIVISOR0 5
        PCW_FCLK0_PERIPHERAL_DIVISOR1 4
        PCW_IOPLL_CTRL_FBDIV 30
        PCW_IO_IO_PLL_FREQMHZ 1000.000
    } {
        set pattern [format {<PARAMETER NAME="%s" VALUE="[^"]*"/>} $name]
        set replacement [format {<PARAMETER NAME="%s" VALUE="%s"/>} $name $value]
        if {[regsub -all $pattern $data $replacement data] != 1} {
            error "Expected one clock parameter: $name"
        }
    }
    set stream [open $hwh_file w]
    fconfigure $stream -encoding utf-8 -translation lf
    puts -nonewline $stream $data
    close $stream
}
