`include "fold_verify_params.svh"
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
    logic phase_reset_pulse, phase_transaction_active, phase_done;
    logic [31:0] phase_cycles_count, phase_cycles_result;
    logic [31:0] phase_transition_step, phase_result_flags;
    logic [2:0] phase_state_live, phase_state_result;
    logic phase_valid_live, phase_changed_live;
    logic look_request_live;
    logic [31:0] event_sequence_live;
    logic retreat_valid_live, vision_commit_pulse, vision_transaction_active;
    logic phase_stream_latched, phase_prevalid_latched;
    logic [31:0] vision_current_session, vision_current_event, vision_last_frame;
    logic [31:0] vision_cycles_live, vision_result_snapshot;
    logic [1:0] vision_state_live;
    logic [3:0] vision_reason_live, vision_result_reason;
    logic [7:0] vision_stable_count;
    logic vision_result_done;
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
    fold_stage stage_observer(.clk(s_axi_aclk),.rst_n(s_axi_aresetn),
        .reset_session(phase_reset_pulse),.sample_commit(core_valid),
        .phase_stream(phase_stream_latched),.sample_prevalid(phase_prevalid_latched),
        .guard_violations(core_mask),.step(registers[5]),.request_sequence(registers[54]),
        .elapsed_us(registers[33]),
        .measured(m),.transaction_cycles_in(phase_cycles_count),
        .phase_state(phase_state_live),.sample_valid(phase_valid_live),
        .stage_changed(phase_changed_live),.transition_step(phase_transition_step),
        .phase_cycles(phase_cycles_result),.phase_done(phase_done),
        .look_request(look_request_live),.retreat_valid(retreat_valid_live),
        .event_sequence(event_sequence_live));
    fold_vision #(.EVENT_TIMEOUT_CYCLES(`FV_EVENT_TIMEOUT_CYCLES)) vision_observer(
        .clk(s_axi_aclk),.rst_n(s_axi_aresetn),.reset_session(phase_reset_pulse),
        .event_open(look_request_live),.event_session_in(registers[53]),
        .event_sequence_in(event_sequence_live),.retreat_valid(retreat_valid_live),
        .feature_commit(vision_commit_pulse),.feature_session(registers[40]),
        .feature_sequence(registers[41]),.event_rx_us({registers[44],registers[43]}),
        .frame_ready_us({registers[46],registers[45]}),.frame_id(registers[42]),
        .area_bp(registers[47][15:0]),.rectangularity_bp(registers[47][31:16]),
        .cx_px(registers[48][15:0]),.cy_px(registers[48][31:16]),
        .dark_bp(registers[49][15:0]),.feature_flags(registers[49][31:16]),
        .image_width(`FV_IMAGE_WIDTH),.image_height(`FV_IMAGE_HEIGHT),
        .area_min_bp(`FV_VISION_AREA_MIN_BP),.area_max_bp(`FV_VISION_AREA_MAX_BP),
        .rectangularity_min_bp(`FV_VISION_RECTANGULARITY_MIN_BP),.rectangularity_max_bp(`FV_VISION_RECTANGULARITY_MAX_BP),
        .dark_min_bp(`FV_VISION_DARK_MIN_BP),.dark_max_bp(`FV_VISION_DARK_MAX_BP),
        .current_session(vision_current_session),.current_event_sequence(vision_current_event),
        .last_frame_id(vision_last_frame),.verify_state(vision_state_live),
        .current_reason(vision_reason_live),.result_reason(vision_result_reason),
        .stable_count(vision_stable_count),.result_done(vision_result_done),
        .vision_cycles(vision_cycles_live));
    wire write_allowed = !s_axi_bvalid && core_ready && !start_pending &&
        !phase_transaction_active && !vision_transaction_active;
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
                 (offset==132 || offset==136 || offset==140 ||
                  offset==212 || offset==216) ||
                 (offset>=160 && offset<=196) || offset==200 ||
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
                ((writable(address) && address!=WD_ACTION_COMMIT_ADDR && address!=16'h008c &&
                  address!=16'h00c8) ||
                 address==0 || address==8 ||
                 address==64 || address==68 || address==72 ||
                 address==144 || address==148 || address==152 || address==156 ||
                 address==220 || address==228 ||
                 address==204 || address==208 || address==224 || address==232 || address==236 ||
                 address==WD_STATUS_ADDR || address==WD_AGE_LO_ADDR ||
                 address==WD_AGE_HI_ADDR || address==WD_TIMEOUT_COUNT_ADDR ||
                 address==WD_LAST_GAP_LO_ADDR || address==WD_LAST_GAP_HI_ADDR);
        end
    endfunction
    function automatic logic [31:0] read_value(input logic [15:0] address);
        begin
            case(address)
                0: read_value=`FV_BUILD_ID;
                4: read_value=0;
                8: read_value={30'd0,done_sticky,(!core_ready || start_pending || phase_transaction_active || vision_transaction_active)};
                64: read_value=core_mask;
                68: read_value=core_events;
                72: read_value=core_phase;
                132: read_value=registers[33];
                136: read_value=registers[34] & 32'h3;
                140: read_value=0;
                144: read_value={22'd0,phase_result_flags[1],phase_result_flags[0],5'd0,phase_state_result};
                148: read_value=phase_transition_step;
                152: read_value=`FV_PHASE_CONFIG_CRC;
                156: read_value=phase_cycles_result;
                204: read_value=vision_result_snapshot;
                208: read_value=vision_cycles_live;
                220: read_value=(vision_state_live==2'd1) ? event_sequence_live : 32'hffffffff;
                228: read_value=event_sequence_live;
                224: read_value={8'd0,vision_stable_count,7'd0,
                                  (vision_state_live==2'd1),vision_reason_live,2'd0,vision_state_live};
                232: read_value=vision_last_frame;
                236: read_value=vision_current_session;
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
            phase_reset_pulse<=0; phase_transaction_active<=0; phase_cycles_count<=0;
            phase_stream_latched<=0; phase_prevalid_latched<=0;
            phase_state_result<=0; phase_result_flags<=0;
            vision_commit_pulse<=0; vision_transaction_active<=0; vision_result_snapshot<=0;
            watchdog_periodic_expected<=0;
            aw_pending<=0; awaddr_pending<=0;
            w_pending<=0; wdata_pending<=0; wstrb_pending<=0;
            for(i=0;i<160;i=i+1) registers[i]<=0;
        end else begin
            phase_reset_pulse<=0;
            vision_commit_pulse<=0;
            if(phase_transaction_active && phase_cycles_count!=32'hffffffff)
                phase_cycles_count<=phase_cycles_count+1'b1;
            if(start_pending && core_ready) start_pending<=0;
            if(phase_done) begin
                phase_transaction_active<=0;
                phase_state_result<=phase_state_live;
                phase_result_flags<={30'd0,phase_changed_live,phase_valid_live};
                done_sticky<=1;
            end
            if(vision_result_done) begin
                vision_transaction_active<=0;
                vision_result_snapshot<={1'b1,7'd0,vision_stable_count,
                                         4'd0,vision_result_reason,4'd0,{2'd0,vision_state_live}};
                done_sticky<=1;
            end
            if(s_axi_bvalid && s_axi_bready) s_axi_bvalid<=0;
            if (write_commit) begin
                aw_pending<=0; w_pending<=0;
                s_axi_bvalid<=1;
                s_axi_bresp<=writable(write_address) ? 2'b00 : 2'b10;
                if(writable(write_address)) begin
                    if(write_address==4) begin
                        if(write_strobe[0] && write_data[0]) begin
                            start_pending<=1; done_sticky<=0;
                            phase_transaction_active<=1; phase_cycles_count<=0;
                            phase_stream_latched<=registers[34][1];
                            phase_prevalid_latched<=registers[34][0];
                            registers[34]<=0;
                        end
                    end else if(write_address==140) begin
                        if(write_strobe[0] && write_data[0]) begin
                            phase_reset_pulse<=1;
                            phase_state_result<=0; phase_result_flags<=0;
                            vision_result_snapshot<=0;
                            phase_stream_latched<=0; phase_prevalid_latched<=0;
                        end
                    end else if(write_address==200) begin
                        if(write_strobe[0] && write_data[0]) begin
                            vision_commit_pulse<=1;
                            vision_transaction_active<=1;
                            vision_result_snapshot<=0;
                            done_sticky<=0;
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
