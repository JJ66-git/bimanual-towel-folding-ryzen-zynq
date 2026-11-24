module tb_fold_stage;
    logic clk=0, rst_n=0, reset_session=0, sample_commit=0;
    logic phase_stream=1, sample_prevalid=1;
    logic [31:0] guard_violations=0, step=0, request_sequence=0, elapsed_us=0;
    logic signed [447:0] measured=0;
    logic [31:0] transaction_cycles_in=21;
    logic [2:0] phase_state;
    logic sample_valid, stage_changed;
    logic [31:0] transition_step, phase_cycles;
    logic phase_done;
    logic look_request;
    logic retreat_valid;
    logic [31:0] event_sequence;

    always #5 clk=~clk;
    fold_stage dut(.*);

    task automatic sample(input logic [31:0] n, input logic [31:0] us,
                          input integer left_grip, input integer right_grip,
                          input integer left_l2, input integer left_l3,
                          input integer right_l2, input integer right_l3,
                          input logic valid);
        begin
            @(negedge clk);
            step=n; request_sequence=n+100; elapsed_us=us; sample_prevalid=valid;
            measured=0;
            measured[6*32 +: 32]=left_grip;
            measured[13*32 +: 32]=right_grip;
            measured[1*32 +: 32]=left_l2;
            measured[2*32 +: 32]=left_l3;
            measured[8*32 +: 32]=right_l2;
            measured[9*32 +: 32]=right_l3;
            sample_commit=1;
            @(negedge clk);
            sample_commit=0;
            if (!phase_done) $fatal(1,"phase result did not complete");
            if (phase_cycles != transaction_cycles_in) $fatal(1,"bad cycle snapshot");
        end
    endtask

    initial begin
        repeat(3) @(negedge clk);
        rst_n=1;
        sample(1,0,30000,30000,0,0,0,0,1);
        sample(2,50000,30000,30000,0,0,0,0,1);
        sample(3,100000,30000,30000,0,0,0,0,1);
        if (phase_state!=1 || !stage_changed || transition_step!=3 || !sample_valid)
            $fatal(1,"OPEN_SEEN transition mismatch");
        sample(4,150000,10000,10000,0,0,0,0,1);
        sample(5,200000,10000,10000,0,0,0,0,1);
        sample(6,250000,10000,10000,0,0,0,0,1);
        if (phase_state!=2 || !stage_changed || transition_step!=6)
            $fatal(1,"GRIP_MOTION transition mismatch");
        sample(7,300000,12000,12000,1600000,-1150000,1600000,-1150000,1);
        sample(8,360000,12000,12000,1950000,-720000,1950000,-720000,1);
        sample(9,420000,12000,12000,1600000,-1000000,1600000,-1000000,1);
        if (phase_state!=3 || !stage_changed || transition_step!=9)
            $fatal(1,"FOLD_MOTION transition mismatch");
        sample(10,470000,10000,10000,500000,-300000,500000,-300000,1);
        sample(11,530000,10000,10000,950000,50000,950000,50000,1);
        sample(12,610000,10000,10000,0,-650000,0,-650000,1);
        if (!look_request || event_sequence!=112)
            $fatal(1,"retreat event identity mismatch");
        sample(13,660000,10000,10000,500000,-300000,500000,-300000,0);
        if (sample_valid || phase_state!=3 || stage_changed)
            $fatal(1,"invalid sample changed latched phase");
        @(negedge clk); reset_session=1;
        @(negedge clk); reset_session=0;
        if (phase_state!=0 || transition_step!=32'hffffffff)
            $fatal(1,"session reset mismatch");
        $display("PASS: fold phase boundaries, qualification, invalidation, and reset");
        $finish;
    end
endmodule
