`timescale 1ns/1ps
module tb_motion_guard;
    logic clk=0, rst_n=0, in_valid=0, in_ready, out_valid;
    logic [31:0] profile_crc, step, flags, feedback_age_us, queue_depth;
    logic signed [31:0] target [0:13], measured [0:13], previous [0:13];
    logic signed [31:0] act [0:13], correction [0:13], previous_correction [0:13];
    logic [31:0] violations, events, scheduled_phase;
    wire [447:0] target_bus, measured_bus, previous_bus, act_bus, correction_bus, previous_correction_bus;
    genvar g;
    generate for(g=0;g<14;g=g+1) begin: pack_vectors
        assign target_bus[g*32 +: 32]=target[g];
        assign measured_bus[g*32 +: 32]=measured[g];
        assign previous_bus[g*32 +: 32]=previous[g];
        assign act_bus[g*32 +: 32]=act[g];
        assign correction_bus[g*32 +: 32]=correction[g];
        assign previous_correction_bus[g*32 +: 32]=previous_correction[g];
    end endgenerate
    motion_guard dut(
        .clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready),
        .profile_crc(profile_crc), .step(step), .flags(flags),
        .feedback_age_us(feedback_age_us), .queue_depth(queue_depth),
        .target(target_bus), .measured(measured_bus), .previous(previous_bus),
        .act(act_bus), .correction(correction_bus),
        .previous_correction(previous_correction_bus), .out_valid(out_valid),
        .violations(violations), .events(events), .scheduled_phase(scheduled_phase)
    );
    always #5 clk=~clk;
    integer file, status, case_count=0, total, i, cycles;
    logic [31:0] expected_mask, expected_events, expected_phase;
    initial begin
        file=$fopen("vectors.txt","r");
        if (!file) $fatal(1,"Missing vectors.txt; run python -m host.make_vectors");
        status=$fscanf(file,"%d",total);
        if (status != 1) $fatal(1,"Missing vector count");
        repeat(3) @(negedge clk);
        rst_n=1;
        for(case_count=0;case_count<total;case_count=case_count+1) begin
            status=$fscanf(file,"%h %d %d %h %h %h %h %d",
                profile_crc,step,flags,feedback_age_us,queue_depth,expected_mask,expected_events,expected_phase);
            if (status != 8) $fatal(1,"Malformed vector header: case %0d",case_count);
            for(i=0;i<14;i=i+1) begin
                status=$fscanf(file,"%d %d %d %d %d %d",
                    target[i],measured[i],previous[i],act[i],correction[i],previous_correction[i]);
                if (status != 6) $fatal(1,"Malformed vector at dimension %0d",i);
            end
            @(negedge clk);
            while(!in_ready) @(negedge clk);
            in_valid=1;
            @(negedge clk); in_valid=0;
            cycles=0;
            while(!out_valid && cycles<30) begin @(negedge clk); cycles=cycles+1; end
            if(!out_valid) $fatal(1,"Core timeout");
            if(violations!==expected_mask || events!==expected_events || scheduled_phase!==expected_phase)
                $fatal(1,"Mismatch case %0d mask %h/%h events %h/%h phase %d/%d",case_count,
                    violations,expected_mask,events,expected_events,scheduled_phase,expected_phase);
        end
        $display("PASS %0d motion guard vectors",case_count);
        $fclose(file); $finish;
    end
endmodule
