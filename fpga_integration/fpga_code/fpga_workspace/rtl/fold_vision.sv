`include "fold_verify_params.svh"
// Event-bound visual evidence accumulator; feature extraction stays on the host.
module fold_vision #(
    parameter integer EVENT_TIMEOUT_CYCLES = `FV_EVENT_TIMEOUT_CYCLES
) (
    input logic clk,
    input logic rst_n,
    input logic reset_session,
    input logic event_open,
    input logic [31:0] event_session_in,
    input logic [31:0] event_sequence_in,
    input logic retreat_valid,
    input logic feature_commit,
    input logic [31:0] feature_session,
    input logic [31:0] feature_sequence,
    input logic [63:0] event_rx_us,
    input logic [63:0] frame_ready_us,
    input logic [31:0] frame_id,
    input logic [15:0] area_bp,
    input logic [15:0] cx_px,
    input logic [15:0] cy_px,
    input logic [15:0] rectangularity_bp,
    input logic [15:0] dark_bp,
    input logic [15:0] feature_flags,
    input logic [15:0] image_width,
    input logic [15:0] image_height,
    input logic [15:0] area_min_bp,
    input logic [15:0] area_max_bp,
    input logic [15:0] rectangularity_min_bp,
    input logic [15:0] rectangularity_max_bp,
    input logic [15:0] dark_min_bp,
    input logic [15:0] dark_max_bp,
    output logic [31:0] current_session,
    output logic [31:0] current_event_sequence,
    output logic [31:0] last_frame_id,
    output logic [1:0] verify_state,
    output logic [3:0] current_reason,
    output logic [3:0] result_reason,
    output logic [7:0] stable_count,
    output logic result_done,
    output logic [31:0] vision_cycles
);
    localparam logic [31:0] UNKNOWN = 32'hffffffff;
    localparam logic [1:0] WAIT_RETREAT=0, CHECKING=1, OBSERVED=2, UNCONFIRMED=3;
    localparam logic [3:0] OK=0, WAIT_REASON=1, WRONG_EVENT=2, STALE_FRAME=3,
        DUPLICATE_FRAME=4, WAIT_STABLE=5, SHAPE_FAILED=6, RETREAT_LOST=7,
        EVIDENCE_TIMEOUT=8, WINDOW_CLOSED=9;
    localparam logic [63:0] WINDOW_US=`FV_TIMING_EVENT_WINDOW_US;
    localparam logic [63:0] FRAME_GAP_US=`FV_TIMING_FRAME_GAP_US;
    localparam logic [63:0] STABLE_SPAN_US=`FV_TIMING_STABLE_SPAN_US;

    logic event_active, event_rx_locked, have_frame;
    logic [63:0] event_age_cycles, locked_event_rx_us;
    logic [63:0] last_frame_time, stable_first_time;
    logic [15:0] stable_first_area, stable_first_cx, stable_first_cy;
    logic [63:0] elapsed_from_event;
    logic [31:0] frame_delta_area, frame_delta_cx, frame_delta_cy;
    logic frame_eligible, shape_valid;
    logic [31:0] stable_next;

    always_comb begin
        elapsed_from_event = frame_ready_us - event_rx_us;
        frame_delta_area = (area_bp >= stable_first_area) ?
            area_bp - stable_first_area : stable_first_area - area_bp;
        frame_delta_cx = (cx_px >= stable_first_cx) ?
            cx_px - stable_first_cx : stable_first_cx - cx_px;
        frame_delta_cy = (cy_px >= stable_first_cy) ?
            cy_px - stable_first_cy : stable_first_cy - cy_px;
        frame_eligible = event_rx_us != 0 && frame_ready_us > event_rx_us &&
            elapsed_from_event <= WINDOW_US &&
            (!event_rx_locked || event_rx_us == locked_event_rx_us) &&
            (!have_frame || (frame_id > last_frame_id && frame_ready_us > last_frame_time));
        shape_valid = feature_flags == 16'd1 && cx_px < image_width && cy_px < image_height &&
            area_bp >= area_min_bp && area_bp <= area_max_bp &&
            rectangularity_bp >= rectangularity_min_bp &&
            rectangularity_bp <= rectangularity_max_bp &&
            dark_bp >= dark_min_bp && dark_bp <= dark_max_bp;
        stable_next = stable_count;
        if (!have_frame || frame_ready_us - last_frame_time > FRAME_GAP_US ||
            frame_delta_area > `FV_VISION_AREA_DELTA_BP || frame_delta_cx > `FV_VISION_CENTER_DELTA_PX || frame_delta_cy > `FV_VISION_CENTER_DELTA_PX)
            stable_next = 1;
        else if (stable_count != 255)
            stable_next = stable_count + 1;
    end

    always_ff @(posedge clk) begin
        if (!rst_n || reset_session) begin
            event_active <= 0;
            event_rx_locked <= 0;
            have_frame <= 0;
            event_age_cycles <= 0;
            locked_event_rx_us <= 0;
            current_session <= 0;
            current_event_sequence <= UNKNOWN;
            last_frame_id <= UNKNOWN;
            last_frame_time <= 0;
            stable_first_time <= 0;
            stable_first_area <= 0;
            stable_first_cx <= 0;
            stable_first_cy <= 0;
            verify_state <= WAIT_RETREAT;
            current_reason <= WAIT_REASON;
            result_reason <= WAIT_REASON;
            stable_count <= 0;
            result_done <= 0;
            vision_cycles <= 0;
        end else begin
            result_done <= 0;
            if (event_open && !event_active) begin
                event_active <= 1;
                event_rx_locked <= 0;
                have_frame <= 0;
                event_age_cycles <= 0;
                current_session <= event_session_in;
                current_event_sequence <= event_sequence_in;
                last_frame_id <= UNKNOWN;
                verify_state <= CHECKING;
                current_reason <= WAIT_STABLE;
                stable_count <= 0;
            end else if (event_active && verify_state == CHECKING) begin
                if (event_age_cycles < EVENT_TIMEOUT_CYCLES)
                    event_age_cycles <= event_age_cycles + 1'b1;
                if (event_age_cycles >= EVENT_TIMEOUT_CYCLES-1) begin
                    verify_state <= UNCONFIRMED;
                    current_reason <= EVIDENCE_TIMEOUT;
                    stable_count <= 0;
                end else if (!retreat_valid) begin
                    stable_count <= 0;
                    current_reason <= RETREAT_LOST;
                end
            end

            if (feature_commit) begin
                vision_cycles <= 0;
                result_done <= 1;
                if (event_active && verify_state == CHECKING &&
                    event_age_cycles >= EVENT_TIMEOUT_CYCLES-1) begin
                    verify_state <= UNCONFIRMED;
                    current_reason <= EVIDENCE_TIMEOUT;
                    result_reason <= EVIDENCE_TIMEOUT;
                    stable_count <= 0;
                end else if (!event_active) result_reason <= WAIT_REASON;
                else if (feature_session != current_session || feature_sequence != current_event_sequence) begin
                    result_reason <= WRONG_EVENT;
                end else if (verify_state != CHECKING) begin
                    result_reason <= WINDOW_CLOSED;
                end else if (!frame_eligible) begin
                    if (have_frame && frame_id == last_frame_id)
                        result_reason <= DUPLICATE_FRAME;
                    else result_reason <= STALE_FRAME;
                end else begin
                    if (!event_rx_locked) begin
                        event_rx_locked <= 1;
                        locked_event_rx_us <= event_rx_us;
                    end
                    have_frame <= 1;
                    last_frame_id <= frame_id;
                    last_frame_time <= frame_ready_us;
                    if (!retreat_valid) begin
                        stable_count <= 0;
                        current_reason <= RETREAT_LOST;
                        result_reason <= RETREAT_LOST;
                    end else if (!shape_valid) begin
                        stable_count <= 0;
                        current_reason <= SHAPE_FAILED;
                        result_reason <= SHAPE_FAILED;
                    end else begin
                        stable_count <= stable_next[7:0];
                        if (stable_next == 1) begin
                            stable_first_time <= frame_ready_us;
                            stable_first_area <= area_bp;
                            stable_first_cx <= cx_px;
                            stable_first_cy <= cy_px;
                        end
                        if (stable_next >= `FV_VISION_MIN_FRAMES &&
                            frame_ready_us - stable_first_time >= STABLE_SPAN_US) begin
                            verify_state <= OBSERVED;
                            current_reason <= OK;
                            result_reason <= OK;
                            stable_count <= stable_next[7:0];
                        end else begin
                            current_reason <= WAIT_STABLE;
                            result_reason <= WAIT_STABLE;
                        end
                    end
                end
            end
        end
    end
endmodule
