// Monitor-only action-stream watchdog. PS must report only validated new commits.
module link_watchdog #(
    parameter integer TIMEOUT_CYCLES = 25000000
) (
    input logic clk,
    input logic rst_n,
    input logic periodic_expected,
    input logic action_commit_accepted,
    output logic armed,
    output logic expired,
    output logic [63:0] age_cycles,
    output logic [31:0] timeout_count,
    output logic [63:0] last_gap_cycles
);
    localparam logic [63:0] TIMEOUT_VALUE = TIMEOUT_CYCLES;
    logic periodic_previous;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            periodic_previous <= 0;
            armed <= 0;
            expired <= 0;
            age_cycles <= 0;
            timeout_count <= 0;
            last_gap_cycles <= 0;
        end else begin
            periodic_previous <= periodic_expected;

            if (!periodic_expected) begin
                armed <= 0;
                age_cycles <= 0;
            end else if (!periodic_previous) begin
                expired <= 0;
                age_cycles <= 0;
                armed <= action_commit_accepted;
            end else if (action_commit_accepted) begin
                if (armed) last_gap_cycles <= age_cycles + 64'd1;
                age_cycles <= 0;
                armed <= 1;
                expired <= 0;
            end else if (armed) begin
                if (age_cycles != 64'hffffffffffffffff)
                    age_cycles <= age_cycles + 64'd1;
                if (!expired && age_cycles >= TIMEOUT_VALUE - 64'd1) begin
                    expired <= 1;
                    if (timeout_count != 32'hffffffff)
                        timeout_count <= timeout_count + 1'b1;
                end
            end
        end
    end
endmodule
