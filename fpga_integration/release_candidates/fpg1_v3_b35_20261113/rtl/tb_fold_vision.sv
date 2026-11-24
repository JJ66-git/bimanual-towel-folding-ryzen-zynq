module tb_fold_vision;
    logic clk=0, rst_n=0, reset_session=0, event_open=0, retreat_valid=1, feature_commit=0;
    logic [31:0] event_session_in=7, event_sequence_in=21;
    logic [31:0] feature_session=7, feature_sequence=21, frame_id=0;
    logic [63:0] event_rx_us=1000000, frame_ready_us=0;
    logic [15:0] area_bp=4300, cx_px=320, cy_px=240;
    logic [15:0] rectangularity_bp=8000, dark_bp=1000, feature_flags=1;
    logic [15:0] image_width=640, image_height=480;
    logic [15:0] area_min_bp=4000, area_max_bp=4700;
    logic [15:0] rectangularity_min_bp=0, rectangularity_max_bp=10000;
    logic [15:0] dark_min_bp=0, dark_max_bp=10000;
    logic [31:0] current_session, current_event_sequence, last_frame_id, vision_cycles;
    logic [1:0] verify_state;
    logic [3:0] current_reason, result_reason;
    logic [7:0] stable_count;
    logic result_done;

    always #5 clk=~clk;
    fold_vision #(.EVENT_TIMEOUT_CYCLES(32)) dut(.*);

    task automatic feature(input logic [31:0] id, input logic [63:0] ready);
        begin
            @(negedge clk); frame_id=id; frame_ready_us=ready; feature_commit=1;
            @(negedge clk); feature_commit=0;
            if (!result_done) $fatal(1,"feature result missing");
        end
    endtask

    initial begin
        repeat(3) @(negedge clk); rst_n=1;
        @(negedge clk); event_open=1;
        @(negedge clk); event_open=0;
        feature(1,1050000);
        if (verify_state!=1 || current_reason!=5 || result_reason!=5 || stable_count!=1)
            $fatal(1,"first stable frame mismatch");
        feature_session=8;
        feature(2,1150000);
        if (current_reason!=5 || result_reason!=2 || stable_count!=1)
            $fatal(1,"wrong event changed current state");
        feature_session=7;
        feature(2,1150000);
        feature(3,1250000);
        if (verify_state!=2 || current_reason!=0 || result_reason!=0 || stable_count!=3)
            $fatal(1,"stable visual confirmation mismatch");
        feature(4,1300000);
        if (verify_state!=2 || result_reason!=9 || current_reason!=0)
            $fatal(1,"terminal result was not immutable");
        @(negedge clk); reset_session=1;
        @(negedge clk); reset_session=0; event_open=1;
        @(negedge clk); event_open=0;
        repeat(34) @(negedge clk);
        if (verify_state!=3 || current_reason!=8)
            $fatal(1,"autonomous timeout mismatch");
        $display("PASS: vision identity, stable frames, terminal latch, and autonomous timeout");
        $finish;
    end
endmodule
