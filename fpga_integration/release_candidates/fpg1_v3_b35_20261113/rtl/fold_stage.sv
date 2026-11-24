`include "fold_verify_params.svh"
// Read-only fold phase observer. It has no actuator or watchdog connection.
module fold_stage (
    input logic clk,
    input logic rst_n,
    input logic reset_session,
    input logic sample_commit,
    input logic phase_stream,
    input logic sample_prevalid,
    input logic [31:0] guard_violations,
    input logic [31:0] step,
    input logic [31:0] request_sequence,
    input logic [31:0] elapsed_us,
    input logic signed [447:0] measured,
    input logic [31:0] transaction_cycles_in,
    output logic [2:0] phase_state,
    output logic sample_valid,
    output logic stage_changed,
    output logic [31:0] transition_step,
    output logic [31:0] phase_cycles,
    output logic phase_done,
    output logic look_request,
    output logic retreat_valid,
    output logic [31:0] event_sequence
);
    localparam logic [31:0] UNKNOWN = 32'hffffffff;
    localparam logic [31:0] MAX_GAP_US = `FV_TIMING_OBSERVATION_GAP_US;
    localparam logic [31:0] OPEN_HOLD_US = `FV_STAGE_OPEN_HOLD_US;
    localparam logic [31:0] GRIP_HOLD_US = `FV_STAGE_GRIP_HOLD_US;
    localparam logic [31:0] FOLD_HOLD_US = `FV_STAGE_FOLD_HOLD_US;
    localparam logic [31:0] LOCAL_TIMEOUT_CYCLES = `FV_LOCAL_TIMEOUT_CYCLES;

    logic [31:0] input_step, input_elapsed;
    logic [31:0] candidate_first_us;
    logic [2:0] candidate_count;
    logic candidate_active;
    logic [31:0] retreat_first_us;
    logic [2:0] retreat_count;
    logic retreat_active, event_latched;
    logic [31:0] since_valid_cycles;
    logic have_valid_sample, retreat_qualified;
    logic condition_now;
    logic [31:0] hold_us;
    logic retreat_condition;
    logic signed [31:0] l2, l3, lg, r2, r3, rg;

    always_comb begin
        l2 = $signed(measured[1*32 +: 32]);
        l3 = $signed(measured[2*32 +: 32]);
        lg = $signed(measured[6*32 +: 32]);
        r2 = $signed(measured[8*32 +: 32]);
        r3 = $signed(measured[9*32 +: 32]);
        rg = $signed(measured[13*32 +: 32]);
        condition_now = 1'b0;
        hold_us = OPEN_HOLD_US;
        case (phase_state)
            3'd0: condition_now = (lg >= `FV_STAGE_OPEN_GRIPPER_MIN) && (rg >= `FV_STAGE_OPEN_GRIPPER_MIN);
            3'd1: condition_now = (lg <= `FV_STAGE_GRIP_GRIPPER_MAX) && (rg <= `FV_STAGE_GRIP_GRIPPER_MAX);
            3'd2: begin
                condition_now = (lg <= `FV_STAGE_FOLD_GRIPPER_MAX) && (rg <= `FV_STAGE_FOLD_GRIPPER_MAX) &&
                    (l2 >= `FV_STAGE_FOLD_J2_MIN) && (l2 <= `FV_STAGE_FOLD_J2_MAX) &&
                    (r2 >= `FV_STAGE_FOLD_J2_MIN) && (r2 <= `FV_STAGE_FOLD_J2_MAX) &&
                    (l3 >= `FV_STAGE_FOLD_J3_MIN) && (l3 <= `FV_STAGE_FOLD_J3_MAX) &&
                    (r3 >= `FV_STAGE_FOLD_J3_MIN) && (r3 <= `FV_STAGE_FOLD_J3_MAX);
                hold_us = FOLD_HOLD_US;
            end
            default: condition_now = 1'b0;
        endcase
        retreat_condition = (l2 >= `FV_RETREAT_J2_MIN) && (l2 <= `FV_RETREAT_J2_MAX) &&
            (r2 >= `FV_RETREAT_J2_MIN) && (r2 <= `FV_RETREAT_J2_MAX) &&
            (l3 >= `FV_RETREAT_J3_MIN) && (l3 <= `FV_RETREAT_J3_MAX) &&
            (r3 >= `FV_RETREAT_J3_MIN) && (r3 <= `FV_RETREAT_J3_MAX);
    end
    assign retreat_valid = retreat_qualified && (since_valid_cycles <= LOCAL_TIMEOUT_CYCLES);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            phase_state <= 0;
            sample_valid <= 0;
            stage_changed <= 0;
            transition_step <= UNKNOWN;
            phase_cycles <= 0;
            phase_done <= 0;
            input_step <= UNKNOWN;
            input_elapsed <= 0;
            candidate_first_us <= 0;
            candidate_count <= 0;
            candidate_active <= 0;
            retreat_first_us <= 0;
            retreat_count <= 0;
            retreat_active <= 0;
            event_latched <= 0;
            look_request <= 0;
            event_sequence <= UNKNOWN;
            since_valid_cycles <= 0;
            have_valid_sample <= 0;
            retreat_qualified <= 0;
        end else if (reset_session) begin
            phase_state <= 0;
            sample_valid <= 0;
            stage_changed <= 0;
            transition_step <= UNKNOWN;
            phase_cycles <= 0;
            phase_done <= 0;
            input_step <= UNKNOWN;
            input_elapsed <= 0;
            candidate_first_us <= 0;
            candidate_count <= 0;
            candidate_active <= 0;
            retreat_first_us <= 0;
            retreat_count <= 0;
            retreat_active <= 0;
            event_latched <= 0;
            look_request <= 0;
            event_sequence <= UNKNOWN;
            since_valid_cycles <= 0;
            have_valid_sample <= 0;
            retreat_qualified <= 0;
        end else begin
            stage_changed <= 0;
            phase_done <= 0;
            look_request <= 0;
            if (have_valid_sample && since_valid_cycles <= LOCAL_TIMEOUT_CYCLES)
                since_valid_cycles <= since_valid_cycles + 1'b1;
            if (sample_commit) begin
                input_step <= step;
                input_elapsed <= elapsed_us;
                phase_done <= 1;
                phase_cycles <= !phase_stream ? 0 :
                    (transaction_cycles_in > 32'hfffffffe ? 32'hfffffffe : transaction_cycles_in);
                if (!phase_stream) begin
                    sample_valid <= 0;
                end else begin
                    sample_valid <= sample_prevalid && (guard_violations == 0) &&
                                    (elapsed_us != UNKNOWN);
                    if (!(sample_prevalid && (guard_violations == 0) &&
                          (elapsed_us != UNKNOWN))) begin
                        candidate_active <= 0;
                        candidate_count <= 0;
                        retreat_active <= 0;
                        retreat_count <= 0;
                        have_valid_sample <= 0;
                        retreat_qualified <= 0;
                        since_valid_cycles <= 0;
                    end else begin
                        if (have_valid_sample)
                            since_valid_cycles <= 0;
                        have_valid_sample <= 1;
                        retreat_qualified <= (phase_state == 3'd3) && retreat_condition;
                        if (phase_state == 3'd3 && !event_latched && retreat_condition) begin
                            if (!retreat_active ||
                                (have_valid_sample && since_valid_cycles > LOCAL_TIMEOUT_CYCLES) ||
                                elapsed_us <= input_elapsed ||
                                elapsed_us - input_elapsed > MAX_GAP_US) begin
                                retreat_active <= 1;
                                retreat_first_us <= elapsed_us;
                                retreat_count <= 1;
                            end else begin
                                if (retreat_count < `FV_RETREAT_MIN_SAMPLES) retreat_count <= retreat_count + 1'b1;
                                if (retreat_count >= (`FV_RETREAT_MIN_SAMPLES-1) &&
                                    elapsed_us - retreat_first_us >= `FV_RETREAT_HOLD_US) begin
                                    event_latched <= 1;
                                    event_sequence <= request_sequence;
                                    look_request <= 1;
                                    retreat_active <= 0;
                                    retreat_count <= 0;
                                end
                            end
                        end else if (phase_state != 3'd3 || !retreat_condition) begin
                            retreat_active <= 0;
                            retreat_count <= 0;
                        end
                        if (!candidate_active ||
                            (have_valid_sample && since_valid_cycles > LOCAL_TIMEOUT_CYCLES) ||
                            elapsed_us <= input_elapsed ||
                            elapsed_us - input_elapsed > MAX_GAP_US) begin
                            candidate_active <= 0;
                            candidate_count <= 0;
                            if (condition_now) begin
                                candidate_active <= 1;
                                candidate_first_us <= elapsed_us;
                                candidate_count <= 1;
                            end
                        end else if (!condition_now) begin
                            candidate_active <= 0;
                            candidate_count <= 0;
                        end else begin
                            if (candidate_count < `FV_STAGE_MIN_SAMPLES) candidate_count <= candidate_count + 1'b1;
                            if ((candidate_count >= (`FV_STAGE_MIN_SAMPLES-1)) &&
                                (elapsed_us - candidate_first_us >= hold_us)) begin
                                phase_state <= phase_state + 1'b1;
                                transition_step <= step;
                                stage_changed <= 1;
                                candidate_active <= 0;
                                candidate_count <= 0;
                            end
                        end
                    end
                end
            end
        end
    end
endmodule
