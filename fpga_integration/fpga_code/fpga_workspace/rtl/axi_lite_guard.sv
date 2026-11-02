// AXI4-Lite register wrapper, single outstanding write and read response.
// AW and W are independently buffered; masters hold VALID until READY.
// PS and guard use the same clock/reset. No DMA, interrupts or actuator output.
module axi_lite_guard #(
    parameter integer WATCHDOG_TIMEOUT_CYCLES = 25000000
) (
    input logic s_axi_aclk, input logic s_axi_aresetn,
    input logic [15:0] s_axi_awaddr, input logic [2:0] s_axi_awprot,
    input logic s_axi_awvalid, output logic s_axi_awready,
    input logic [31:0] s_axi_wdata, input logic [3:0] s_axi_wstrb,
    input logic s_axi_wvalid, output logic s_axi_wready,
    output logic [1:0] s_axi_bresp, output logic s_axi_bvalid, input logic s_axi_bready,
    input logic [15:0] s_axi_araddr, input logic [2:0] s_axi_arprot,
    input logic s_axi_arvalid, output logic s_axi_arready,
    output logic [31:0] s_axi_rdata, output logic [1:0] s_axi_rresp,
    output logic s_axi_rvalid, input logic s_axi_rready
);
    logic [31:0] registers [0:159];
    logic start_pending, core_ready, core_valid, done_sticky;
    logic watchdog_periodic_expected;
    logic watchdog_armed, watchdog_expired;
    logic [63:0] watchdog_age_cycles, watchdog_last_gap_cycles;
    logic [31:0] watchdog_timeout_count;
    logic [31:0] core_mask, core_events, core_phase;
    logic aw_pending, w_pending;
    logic [15:0] awaddr_pending;
    logic [31:0] wdata_pending;
    logic [3:0] wstrb_pending;
    localparam logic [15:0] WD_CONTROL_ADDR = 16'h004c;
    localparam logic [15:0] WD_ACTION_COMMIT_ADDR = 16'h0050;
    localparam logic [15:0] WD_STATUS_ADDR = 16'h0054;
    localparam logic [15:0] WD_AGE_LO_ADDR = 16'h0058;
    localparam logic [15:0] WD_AGE_HI_ADDR = 16'h005c;
    localparam logic [15:0] WD_TIMEOUT_COUNT_ADDR = 16'h0060;
    localparam logic [15:0] WD_LAST_GAP_LO_ADDR = 16'h0064;
    localparam logic [15:0] WD_LAST_GAP_HI_ADDR = 16'h0068;
    wire [447:0] t, m, p, a, c, pc;
    genvar k;
    generate for(k=0;k<14;k=k+1) begin: vectors
        assign t[k*32 +: 32]=registers[64+k]; assign m[k*32 +: 32]=registers[80+k];
        assign p[k*32 +: 32]=registers[96+k]; assign a[k*32 +: 32]=registers[112+k];
        assign c[k*32 +: 32]=registers[128+k]; assign pc[k*32 +: 32]=registers[144+k];
    end endgenerate
    motion_guard guard(.clk(s_axi_aclk),.rst_n(s_axi_aresetn),
        .in_valid(start_pending),.in_ready(core_ready),
        .profile_crc(registers[4]),.step(registers[5]),.flags(registers[6]),
        .feedback_age_us(registers[7]),.queue_depth(registers[8]),
        .target(t),.measured(m),.previous(p),.act(a),.correction(c),.previous_correction(pc),
        .out_valid(core_valid),.violations(core_mask),.events(core_events),.scheduled_phase(core_phase));
    wire write_allowed = !s_axi_bvalid && core_ready && !start_pending;
    wire aw_fire = s_axi_awvalid && s_axi_awready;
    wire w_fire = s_axi_wvalid && s_axi_wready;
    wire have_aw = aw_pending || aw_fire;
    wire have_w = w_pending || w_fire;
    wire [15:0] write_address = aw_pending ? awaddr_pending : s_axi_awaddr;
    wire [31:0] write_data = w_pending ? wdata_pending : s_axi_wdata;
    wire [3:0] write_strobe = w_pending ? wstrb_pending : s_axi_wstrb;
    wire write_commit = write_allowed && have_aw && have_w;
    assign s_axi_awready=write_allowed && !aw_pending;
    assign s_axi_wready=write_allowed && !w_pending;
    assign s_axi_arready=!s_axi_rvalid;

    function automatic logic writable(input logic [15:0] address);
        integer offset, index;
        begin
            offset=address;
            index=(offset & 63)>>2;
            writable=(address[1:0]==0) &&
                ((offset==4) || (offset>=16 && offset<=32) ||
                 (address==WD_CONTROL_ADDR) || (address==WD_ACTION_COMMIT_ADDR) ||
                 (offset>=256 && offset<640 && index<14));
        end
    endfunction
    function automatic logic [31:0] merge_bytes(input logic [31:0] old_value, new_value,
                                               input logic [3:0] enables);
        integer b;
        begin
            merge_bytes=old_value;
            for(b=0;b<4;b=b+1)
                if(enables[b]) merge_bytes[b*8 +:8]=new_value[b*8 +:8];
        end
    endfunction
    function automatic logic readable(input logic [15:0] address);
        begin
            readable=(address[1:0]==0) &&
                ((writable(address) && address!=WD_ACTION_COMMIT_ADDR) ||
                 address==0 || address==8 ||
                 address==64 || address==68 || address==72 ||
                 address==WD_STATUS_ADDR || address==WD_AGE_LO_ADDR ||
                 address==WD_AGE_HI_ADDR || address==WD_TIMEOUT_COUNT_ADDR ||
                 address==WD_LAST_GAP_LO_ADDR || address==WD_LAST_GAP_HI_ADDR);
        end
    endfunction
    function automatic logic [31:0] read_value(input logic [15:0] address);
        begin
            case(address)
                0: read_value=32'h46504732;
                4: read_value=0;
                8: read_value={30'd0,done_sticky,(!core_ready || start_pending)};
                64: read_value=core_mask;
                68: read_value=core_events;
                72: read_value=core_phase;
                WD_CONTROL_ADDR: read_value={31'd0,watchdog_periodic_expected};
                WD_STATUS_ADDR: read_value={29'd0,watchdog_expired,watchdog_armed,
                                             watchdog_periodic_expected};
                WD_AGE_LO_ADDR: read_value=watchdog_age_cycles[31:0];
                WD_AGE_HI_ADDR: read_value=watchdog_age_cycles[63:32];
                WD_TIMEOUT_COUNT_ADDR: read_value=watchdog_timeout_count;
                WD_LAST_GAP_LO_ADDR: read_value=watchdog_last_gap_cycles[31:0];
                WD_LAST_GAP_HI_ADDR: read_value=watchdog_last_gap_cycles[63:32];
                default: read_value=(address>>2)<160 ? registers[address>>2] : 0;
            endcase
        end
    endfunction

    wire watchdog_action_commit = write_commit && writable(write_address) &&
        write_address==WD_ACTION_COMMIT_ADDR && write_strobe[0] && write_data[0];
    link_watchdog #(.TIMEOUT_CYCLES(WATCHDOG_TIMEOUT_CYCLES)) watchdog (
        .clk(s_axi_aclk), .rst_n(s_axi_aresetn),
        .periodic_expected(watchdog_periodic_expected),
        .action_commit_accepted(watchdog_action_commit),
        .armed(watchdog_armed), .expired(watchdog_expired),
        .age_cycles(watchdog_age_cycles),
        .timeout_count(watchdog_timeout_count),
        .last_gap_cycles(watchdog_last_gap_cycles)
    );

    integer i;
    always_ff @(posedge s_axi_aclk) begin
        if(!s_axi_aresetn) begin
            s_axi_bvalid<=0; s_axi_bresp<=0;
            s_axi_rvalid<=0; s_axi_rresp<=0; s_axi_rdata<=0;
            start_pending<=0; done_sticky<=0;
            watchdog_periodic_expected<=0;
            aw_pending<=0; awaddr_pending<=0;
            w_pending<=0; wdata_pending<=0; wstrb_pending<=0;
            for(i=0;i<160;i=i+1) registers[i]<=0;
        end else begin
            if(start_pending && core_ready) start_pending<=0;
            if(core_valid) done_sticky<=1;
            if(s_axi_bvalid && s_axi_bready) s_axi_bvalid<=0;
            if (write_commit) begin
                aw_pending<=0; w_pending<=0;
                s_axi_bvalid<=1;
                s_axi_bresp<=writable(write_address) ? 2'b00 : 2'b10;
                if(writable(write_address)) begin
                    if(write_address==4) begin
                        if(write_strobe[0] && write_data[0]) begin
                            start_pending<=1; done_sticky<=0;
                        end
                    end else if(write_address==WD_CONTROL_ADDR) begin
                        if(write_strobe[0])
                            watchdog_periodic_expected<=write_data[0];
                    end else if(write_address==WD_ACTION_COMMIT_ADDR) begin
                        // The write handshake itself is the PL-side commit event.
                    end else registers[write_address>>2]<=merge_bytes(
                        registers[write_address>>2],write_data,write_strobe);
                end
            end else begin
                if (aw_fire) begin
                    aw_pending<=1;
                    awaddr_pending<=s_axi_awaddr;
                end
                if (w_fire) begin
                    w_pending<=1;
                    wdata_pending<=s_axi_wdata;
                    wstrb_pending<=s_axi_wstrb;
                end
            end
            if(s_axi_rvalid && s_axi_rready) s_axi_rvalid<=0;
            if(s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid<=1;
                s_axi_rresp<=readable(s_axi_araddr) ? 2'b00 : 2'b10;
                s_axi_rdata<=readable(s_axi_araddr) ? read_value(s_axi_araddr) : 0;
            end
        end
    end
endmodule
