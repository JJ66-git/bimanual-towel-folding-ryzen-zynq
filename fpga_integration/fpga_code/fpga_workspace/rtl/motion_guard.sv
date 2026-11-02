// Board-independent checker only. No UART/MAC, CAN, enable or actuator output.
// Full request vectors are latched on in_valid && in_ready.
module motion_guard (
    input logic clk, input logic rst_n,
    input logic in_valid, output logic in_ready,
    input logic [31:0] profile_crc, step, flags, feedback_age_us, queue_depth,
    input logic signed [447:0] target, measured, previous,
    input logic signed [447:0] act, correction, previous_correction,
    output logic out_valid,
    output logic [31:0] violations, events, scheduled_phase
);
`include "guard_config.svh"
    logic busy;
    integer index;
    integer j;
    logic [31:0] latched_flags, aggregate, local_mask;
    logic signed [31:0] t [0:13], m [0:13], p [0:13], a [0:13], c [0:13], pc [0:13];
    assign in_ready = !busy;

    function automatic logic [32:0] absdiff(input logic signed [31:0] x, y);
        logic signed [32:0] delta;
        begin
            delta = $signed({x[31],x}) - $signed({y[31],y});
            absdiff = delta < 0 ? -delta : delta;
        end
    endfunction

    always_comb begin
        local_mask = 0;
        if (t[index] < bound_lower(index)-1 || t[index] > bound_upper(index)+1)
            local_mask[0] = 1;
        if (a[index] < bound_lower(index)-1 || a[index] > bound_upper(index)+1)
            local_mask[1] = 1;
        if (index != 6 && index != 13) begin
            if (absdiff(t[index],p[index]) > JOINT_STEP_LIMIT) local_mask[2] = 1;
            if (absdiff(m[index],p[index]) > TRACKING_LIMIT) local_mask[3] = 1;
            if (latched_flags[1]) begin
                if (absdiff(c[index],32'sd0) > CORRECTION_LIMIT) local_mask[4] = 1;
                if (absdiff(c[index],pc[index]) > CORRECTION_STEP_LIMIT) local_mask[5] = 1;
            end
        end else if (t[index] != a[index] || c[index] != 0) local_mask[6] = 1;
        if (latched_flags[0]) begin
            if (index == 6 || index == 13) begin
                if (absdiff(m[index],start_pose(index)) > START_GRIPPER_TOLERANCE) local_mask[7] = 1;
            end else if (absdiff(m[index],start_pose(index)) > START_JOINT_TOLERANCE) local_mask[7] = 1;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            busy <= 0; index <= 0; out_valid <= 0;
            aggregate <= 0; violations <= 0; events <= 0; scheduled_phase <= 0;
            latched_flags <= 0;
        end else begin
            out_valid <= 0;
            if (in_valid && in_ready) begin
                for (j=0;j<14;j=j+1) begin
                    t[j] <= $signed(target[j*32 +: 32]);
                    m[j] <= $signed(measured[j*32 +: 32]);
                    p[j] <= $signed(previous[j*32 +: 32]);
                    a[j] <= $signed(act[j*32 +: 32]);
                    c[j] <= $signed(correction[j*32 +: 32]);
                    pc[j] <= $signed(previous_correction[j*32 +: 32]);
                end
                aggregate <= ((flags[2] && step >= MAX_ACTIONS) ? 32'h100 : 0)
                           | ((profile_crc != PROFILE_CRC) ? 32'h400 : 0)
                           | ((AGE_CHECK_ENABLED && feedback_age_us != 32'hffffffff
                               && feedback_age_us > MAX_FEEDBACK_AGE_US) ? 32'h200 : 0);
                events <= ((flags[2] && (step >= HANDOFF_STEP || flags[3])) ? 1 : 0)
                        | ((flags[2] && step >= MAX_ACTIONS) ? 2 : 0)
                        | ((flags[2] && (step >= HANDOFF_STEP || flags[3]) && queue_depth == 0) ? 4 : 0)
                        | ((feedback_age_us == 32'hffffffff) ? 8 : 0);
                scheduled_phase <= !flags[2] ? 0 : ((step >= HANDOFF_STEP || flags[3]) ? 4 : 3);
                latched_flags <= flags;
                busy <= 1; index <= 0;
            end else if (busy) begin
                aggregate <= aggregate | local_mask;
                if (index == 13) begin
                    violations <= aggregate | local_mask;
                    busy <= 0; out_valid <= 1;
                end else index <= index+1;
            end
        end
    end
endmodule
