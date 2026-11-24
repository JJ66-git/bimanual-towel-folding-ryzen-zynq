`timescale 1ns/1ps
module tb_link_watchdog;
    localparam integer TEST_TIMEOUT_CYCLES = 4;

    logic clk = 0;
    logic rst_n = 0;
    logic periodic_expected = 0;
    logic action_commit_accepted = 0;
    wire armed;
    wire expired;
    wire [63:0] age_cycles;
    wire [31:0] timeout_count;
    wire [63:0] last_gap_cycles;

    link_watchdog #(.TIMEOUT_CYCLES(TEST_TIMEOUT_CYCLES)) dut (
        .clk(clk),
        .rst_n(rst_n),
        .periodic_expected(periodic_expected),
        .action_commit_accepted(action_commit_accepted),
        .armed(armed),
        .expired(expired),
        .age_cycles(age_cycles),
        .timeout_count(timeout_count),
        .last_gap_cycles(last_gap_cycles)
    );

    always #5 clk = ~clk;

    task automatic tick;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        repeat (2) tick();
        if (armed || expired || age_cycles || timeout_count || last_gap_cycles)
            $fatal(1, "reset state mismatch");
        rst_n = 1;

        action_commit_accepted = 1;
        tick();
        action_commit_accepted = 0;
        if (armed || expired || timeout_count)
            $fatal(1, "commit while disarmed must be ignored");

        periodic_expected = 1;
        repeat (6) tick();
        if (armed || expired || timeout_count)
            $fatal(1, "periodic mode must wait for the first accepted action");

        action_commit_accepted = 1;
        tick();
        action_commit_accepted = 0;
        if (!armed || expired || age_cycles != 0)
            $fatal(1, "first accepted action did not arm a fresh interval");

        repeat (TEST_TIMEOUT_CYCLES - 1) tick();
        if (expired || timeout_count != 0 || age_cycles != TEST_TIMEOUT_CYCLES - 1)
            $fatal(1, "watchdog expired before its configured boundary");
        tick();
        if (!expired || timeout_count != 1 || age_cycles != TEST_TIMEOUT_CYCLES)
            $fatal(1, "watchdog did not expire on the configured boundary");
        repeat (5) tick();
        if (!expired || timeout_count != 1)
            $fatal(1, "one quiet interval must produce only one timeout");

        begin : recovery
            logic [63:0] expected_gap;
            expected_gap = age_cycles + 1;
            action_commit_accepted = 1;
            tick();
            action_commit_accepted = 0;
            if (!armed || expired || age_cycles != 0 || last_gap_cycles != expected_gap)
                $fatal(1, "accepted action did not record and clear the expired gap");
        end

        repeat (TEST_TIMEOUT_CYCLES) tick();
        if (!expired || timeout_count != 2)
            $fatal(1, "second quiet interval was not counted once");

        periodic_expected = 0;
        tick();
        if (armed || !expired || timeout_count != 2)
            $fatal(1, "disarm must stop monitoring and preserve timeout history");

        periodic_expected = 1;
        tick();
        if (armed || expired || timeout_count != 2)
            $fatal(1, "new periodic session must wait for its first action");

        $display("PASS: watchdog periodic session, first commit, exact timeout, one-shot count, recovery, and disarm");
        $finish;
    end
endmodule
