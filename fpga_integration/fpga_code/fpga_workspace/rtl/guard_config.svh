// Generated from config/guard_profile.json; signed micro-units.
localparam logic [31:0] PROFILE_CRC = 32'd2404155221;
localparam logic [31:0] HANDOFF_STEP = 32'd300;
localparam logic [31:0] MAX_ACTIONS = 32'd750;
localparam logic [31:0] JOINT_STEP_LIMIT = 32'd1000000;
localparam logic [31:0] TRACKING_LIMIT = 32'd3000000;
localparam logic [31:0] CORRECTION_LIMIT = 32'd150000;
localparam logic [31:0] CORRECTION_STEP_LIMIT = 32'd20010;
localparam logic [31:0] START_JOINT_TOLERANCE = 32'd120000;
localparam logic [31:0] START_GRIPPER_TOLERANCE = 32'd12000;
localparam logic [31:0] AGE_CHECK_ENABLED = 32'd0;
localparam logic [31:0] MAX_FEEDBACK_AGE_US = 32'd0;
function automatic logic signed [31:0] bound_lower(input integer idx);
  case(idx)
    0: bound_lower = -1610000;
    1: bound_lower = -50000;
    2: bound_lower = -2140000;
    3: bound_lower = -1580000;
    4: bound_lower = -1400000;
    5: bound_lower = -1580000;
    6: bound_lower = 0;
    7: bound_lower = -1610000;
    8: bound_lower = -50000;
    9: bound_lower = -2140000;
    10: bound_lower = -1580000;
    11: bound_lower = -1400000;
    12: bound_lower = -1580000;
    13: bound_lower = 0;
    default: bound_lower = 0;
  endcase
endfunction
function automatic logic signed [31:0] bound_upper(input integer idx);
  case(idx)
    0: bound_upper = 1610000;
    1: bound_upper = 2540000;
    2: bound_upper = 60000;
    3: bound_upper = 1580000;
    4: bound_upper = 1400000;
    5: bound_upper = 1580000;
    6: bound_upper = 80000;
    7: bound_upper = 1610000;
    8: bound_upper = 2540000;
    9: bound_upper = 60000;
    10: bound_upper = 1580000;
    11: bound_upper = 1400000;
    12: bound_upper = 1580000;
    13: bound_upper = 80000;
    default: bound_upper = 0;
  endcase
endfunction
function automatic logic signed [31:0] start_pose(input integer idx);
  case(idx)
    0: start_pose = 21962;
    1: start_pose = 2913;
    2: start_pose = 6193;
    3: start_pose = -158985;
    4: start_pose = 59100;
    5: start_pose = 135610;
    6: start_pose = 0;
    7: start_pose = -40453;
    8: start_pose = 2582;
    9: start_pose = 5739;
    10: start_pose = 10187;
    11: start_pose = 117154;
    12: start_pose = -91755;
    13: start_pose = 6300;
    default: start_pose = 0;
  endcase
endfunction
