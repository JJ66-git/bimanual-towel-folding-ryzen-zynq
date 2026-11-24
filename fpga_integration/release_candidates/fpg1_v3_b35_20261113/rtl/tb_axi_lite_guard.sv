`include "fold_verify_params.svh"
`timescale 1ns/1ps
module tb_axi_lite_guard;
    logic clk=0, rst_n=0;
    logic [15:0] awaddr=0, araddr=0;
    logic [2:0] awprot=0, arprot=0;
    logic awvalid=0, wvalid=0, bready=0, arvalid=0, rready=0;
    logic [31:0] wdata=0;
    logic [3:0] wstrb=4'hf;
    wire awready, wready, bvalid, arready, rvalid;
    wire [1:0] bresp, rresp;
    wire [31:0] rdata;

    axi_lite_guard #(.WATCHDOG_TIMEOUT_CYCLES(128)) dut(
        .s_axi_aclk(clk), .s_axi_aresetn(rst_n),
        .s_axi_awaddr(awaddr), .s_axi_awprot(awprot),
        .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arprot(arprot),
        .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready)
    );

    always #5 clk=~clk;

    task automatic send_write(input [15:0] address, input [31:0] data,
                              input [3:0] strobe, input integer order,
                              input [1:0] expected_response);
        integer timeout;
        begin
            @(negedge clk);
            awaddr=address; wdata=data; wstrb=strobe;
            awvalid=(order != 2); wvalid=(order != 1);
            timeout=0;
            if (order == 1) begin
                while (!awready && timeout<20) begin @(posedge clk); timeout=timeout+1; end
                if (!awready) $fatal(1,"AW timeout at %h",address);
                @(posedge clk); @(negedge clk); awvalid=0;
                wvalid=1; timeout=0;
                while (!wready && timeout<20) begin @(posedge clk); timeout=timeout+1; end
                if (!wready) $fatal(1,"W timeout after AW at %h",address);
                @(posedge clk); @(negedge clk); wvalid=0;
            end else if (order == 2) begin
                while (!wready && timeout<20) begin @(posedge clk); timeout=timeout+1; end
                if (!wready) $fatal(1,"W timeout at %h",address);
                @(posedge clk); @(negedge clk); wvalid=0;
                awvalid=1; timeout=0;
                while (!awready && timeout<20) begin @(posedge clk); timeout=timeout+1; end
                if (!awready) $fatal(1,"AW timeout after W at %h",address);
                @(posedge clk); @(negedge clk); awvalid=0;
            end else begin
                timeout=0;
                while ((!awready || !wready) && timeout<20) begin @(posedge clk); timeout=timeout+1; end
                if (!awready || !wready) $fatal(1,"simultaneous write channel timeout at %h",address);
                @(posedge clk); @(negedge clk); awvalid=0; wvalid=0;
            end

            timeout=0;
            while (!bvalid && timeout<20) begin @(negedge clk); timeout=timeout+1; end
            if (!bvalid) $fatal(1,"B response timeout at %h",address);
            if (bresp !== expected_response)
                $fatal(1,"B response at %h: got %b expected %b",address,bresp,expected_response);
            repeat(2) begin
                @(posedge clk);
                if (!bvalid) $fatal(1,"BVALID dropped before BREADY at %h",address);
                if (bresp !== expected_response)
                    $fatal(1,"BRESP changed before BREADY at %h",address);
            end
            @(negedge clk); bready=1;
            @(posedge clk); @(negedge clk); bready=0;
        end
    endtask

    task automatic read_register(input [15:0] address, output reg [31:0] value);
        integer timeout;
        begin
            @(negedge clk); araddr=address; arvalid=1; timeout=0;
            while (!arready && timeout<20) begin @(posedge clk); timeout=timeout+1; end
            if (!arready) $fatal(1,"AR timeout at %h",address);
            @(posedge clk); @(negedge clk); arvalid=0;
            timeout=0;
            while (!rvalid && timeout<20) begin @(negedge clk); timeout=timeout+1; end
            if (!rvalid) $fatal(1,"R response timeout at %h",address);
            if (rresp !== 2'b00) $fatal(1,"Read response at %h: %b",address,rresp);
            value=rdata;
            repeat(2) begin
                @(posedge clk);
                if (!rvalid) $fatal(1,"RVALID dropped before RREADY at %h",address);
                if (rresp !== 2'b00 || rdata !== value)
                    $fatal(1,"Read payload changed before RREADY at %h",address);
            end
            @(negedge clk); rready=1;
            @(posedge clk); @(negedge clk); rready=0;
        end
    endtask

    task automatic read_word(input [15:0] address, input [1:0] expected_response,
                             input [31:0] expected_data);
        reg [31:0] value;
        integer timeout;
        begin
            if (expected_response == 2'b00) begin
                read_register(address,value);
                if (value !== expected_data)
                    $fatal(1,"Read %h: data=%h expected=%h",address,value,expected_data);
            end else begin
                @(negedge clk); araddr=address; arvalid=1; timeout=0;
                while (!arready && timeout<20) begin @(posedge clk); timeout=timeout+1; end
                if (!arready) $fatal(1,"AR timeout at %h",address);
                @(posedge clk); @(negedge clk); arvalid=0;
                timeout=0;
                while (!rvalid && timeout<20) begin @(negedge clk); timeout=timeout+1; end
                if (!rvalid) $fatal(1,"R response timeout at %h",address);
                if (rresp !== expected_response || rdata !== expected_data)
                    $fatal(1,"Read %h: resp=%b data=%h expected resp=%b data=%h",
                        address,rresp,rdata,expected_response,expected_data);
                @(negedge clk); rready=1;
                @(posedge clk); @(negedge clk); rready=0;
            end
        end
    endtask

    integer poll;
    reg [31:0] status_value, watchdog_count_value, watchdog_gap_value;
    reg signed [447:0] sample_vector;

    task automatic submit_fold_sample(input [31:0] n, input [31:0] elapsed,
                                      input integer pose);
        reg [31:0] status;
        integer i;
        integer grip, left_l2, left_l3, right_l2, right_l3;
        begin
            grip=pose==0 ? 40000 : 0;
            left_l2=pose==2 ? 1700000 : (pose==3 ? 500000 : 2913);
            right_l2=pose==2 ? 1700000 : (pose==3 ? 500000 : 2582);
            left_l3=pose==2 ? -900000 : (pose==3 ? -400000 : 6193);
            right_l3=pose==2 ? -900000 : (pose==3 ? -400000 : 5739);
            sample_vector='0;
            sample_vector[0*32 +:32]=21962;
            sample_vector[1*32 +:32]=left_l2;
            sample_vector[2*32 +:32]=left_l3;
            sample_vector[3*32 +:32]=-158985;
            sample_vector[4*32 +:32]=59100;
            sample_vector[5*32 +:32]=135610;
            sample_vector[6*32 +:32]=grip;
            sample_vector[7*32 +:32]=-40453;
            sample_vector[8*32 +:32]=right_l2;
            sample_vector[9*32 +:32]=right_l3;
            sample_vector[10*32 +:32]=10187;
            sample_vector[11*32 +:32]=117154;
            sample_vector[12*32 +:32]=-91755;
            sample_vector[13*32 +:32]=grip;
            send_write(16'h0010,32'h8f4c7f55,4'hf,0,2'b00);
            send_write(16'h0014,n,4'hf,0,2'b00);
            send_write(16'h0018,32'h00000004,4'hf,0,2'b00);
            send_write(16'h001c,32'd1000,4'hf,0,2'b00);
            send_write(16'h0020,32'hffffffff,4'hf,0,2'b00);
            send_write(16'h0084,elapsed,4'hf,0,2'b00);
            send_write(16'h0088,32'd3,4'hf,0,2'b00);
            send_write(16'h00d4,32'd42,4'hf,0,2'b00);
            send_write(16'h00d8,n,4'hf,0,2'b00);
            for (i=0;i<14;i=i+1) begin
                send_write(16'h0100+i*4,sample_vector[i*32 +:32],4'hf,0,2'b00);
                send_write(16'h0140+i*4,sample_vector[i*32 +:32],4'hf,0,2'b00);
                send_write(16'h0180+i*4,sample_vector[i*32 +:32],4'hf,0,2'b00);
                send_write(16'h01c0+i*4,sample_vector[i*32 +:32],4'hf,0,2'b00);
            end
            for (i=0;i<14;i=i+1) begin
                send_write(16'h0200+i*4,32'd0,4'hf,0,2'b00);
                send_write(16'h0240+i*4,32'd0,4'hf,0,2'b00);
            end
            send_write(16'h0004,32'd1,4'hf,0,2'b00);
            poll=0; status=0;
            while (poll<40 && status[1:0] != 2'b10) begin
                read_register(16'h0008,status);
                poll=poll+1;
            end
            if (status[1:0] != 2'b10) $fatal(1,"Fold AXI sample completion timeout");
        end
    endtask

    task automatic submit_visual_feature(input [31:0] id, input [63:0] ready_us);
        reg [31:0] status;
        integer timeout;
        begin
            send_write(16'h00a0,32'd42,4'hf,0,2'b00);
            send_write(16'h00a4,32'd12,4'hf,0,2'b00);
            send_write(16'h00a8,id,4'hf,0,2'b00);
            send_write(16'h00ac,32'd620000,4'hf,0,2'b00);
            send_write(16'h00b0,32'd0,4'hf,0,2'b00);
            send_write(16'h00b4,ready_us[31:0],4'hf,0,2'b00);
            send_write(16'h00b8,ready_us[63:32],4'hf,0,2'b00);
            send_write(16'h00bc,32'h1f4010cc,4'hf,0,2'b00);
            send_write(16'h00c0,32'h00f00140,4'hf,0,2'b00);
            send_write(16'h00c4,32'h000103e8,4'hf,0,2'b00);
            send_write(16'h00c8,32'd1,4'hf,0,2'b00);
            timeout=0; status=0;
            while (timeout<40 && !(status[1] && !status[0])) begin
                read_register(16'h0008,status);
                timeout=timeout+1;
            end
            if (timeout>=40) $fatal(1,"FVE1 AXI feature completion timeout");
            read_register(16'h0008,status);
            if (!status[1]) $fatal(1,"FVE1 AXI result was not latched");
            read_register(16'h00cc,status);
            if (!status[31] || status[23:16] != id || status[15:8] != (id==3 ? 0 : 5))
                $fatal(1,"B35 FVE1 snapshot layout mismatch: %h",status);
        end
    endtask
    initial begin
        repeat(3) @(negedge clk);
        rst_n=1;
        read_word(12'h000,2'b00,32'h46504735);
        read_word(16'h0098,2'b00,`FV_PHASE_CONFIG_CRC);
        send_write(16'h0098,32'd0,4'hf,0,2'b10);
        read_word(16'h0098,2'b00,`FV_PHASE_CONFIG_CRC);

        send_write(16'h1010,32'hdeadbeef,4'hf,0,2'b10);
        read_word(16'h1000,2'b10,32'h00000000);
        read_word(16'h1010,2'b10,32'h00000000);
        read_word(16'h0010,2'b00,32'h00000000);

        read_word(16'h0050,2'b10,32'h00000000);
        send_write(12'h010,32'h8f4c7f55,4'hf,1,2'b00);
        read_word(12'h010,2'b00,32'h8f4c7f55);

        send_write(12'h014,32'h11223344,4'hf,2,2'b00);
        send_write(12'h014,32'haabbccdd,4'b0101,0,2'b00);
        read_word(12'h014,2'b00,32'h11bb33dd);

        send_write(12'h018,32'h00000004,4'hf,0,2'b00);
        send_write(12'h011,32'h00000000,4'hf,0,2'b10);
        read_word(12'h011,2'b10,32'h00000000);

        send_write(12'h01c,32'hffffffff,4'hf,0,2'b00);
        send_write(12'h020,32'h00000000,4'hf,0,2'b00);
        send_write(12'h100,32'd1610002,4'hf,0,2'b00);
        send_write(12'h140,32'd0,4'hf,0,2'b00);
        send_write(12'h180,32'd0,4'hf,0,2'b00);
        send_write(12'h1c0,32'd0,4'hf,0,2'b00);
        send_write(12'h200,32'd0,4'hf,0,2'b00);
        send_write(12'h240,32'd0,4'hf,0,2'b00);
        send_write(12'h014,32'd0,4'hf,0,2'b00);
        send_write(12'h004,32'd1,4'hf,0,2'b00);

        poll=0; status_value=0;
        while (poll<40 && status_value[1:0] != 2'b10) begin
            read_register(12'h008,status_value);
            poll=poll+1;
        end
        if (status_value[1:0] != 2'b10) $fatal(1,"Core completion timeout");
        read_word(12'h040,2'b00,32'h00000005);
        read_word(12'h044,2'b00,32'h00000008);
        read_word(12'h048,2'b00,32'h00000003);

        send_write(16'h008c,32'd1,4'hf,0,2'b00);
        submit_fold_sample(1,0,0);
        submit_fold_sample(2,50000,0);
        submit_fold_sample(3,100000,0);
        submit_fold_sample(4,150000,1);
        submit_fold_sample(5,200000,1);
        submit_fold_sample(6,250000,1);
        submit_fold_sample(7,300000,2);
        submit_fold_sample(8,360000,2);
        submit_fold_sample(9,420000,2);
        submit_fold_sample(10,470000,3);
        submit_fold_sample(11,530000,3);
        submit_fold_sample(12,610000,3);
        read_word(16'h0090,2'b00,32'h00000103);
        read_word(16'h00dc,2'b00,32'd12);
        submit_visual_feature(1,650000);
        submit_visual_feature(2,740000);
        submit_visual_feature(3,840000);
        read_word(16'h00e0,2'b00,32'h00030002);
        read_word(16'h00cc,2'b00,32'h80030002);

        send_write(16'h004c,32'h00000001,4'hf,0,2'b00);
        read_word(16'h004c,2'b00,32'h00000001);
        send_write(16'h0050,32'h00000001,4'h0,0,2'b00);
        read_word(16'h0054,2'b00,32'h00000001);
        send_write(16'h0050,32'h00000001,4'hf,0,2'b00);
        read_register(16'h0054,status_value);
        if (status_value !== 32'h00000003)
            $fatal(1,"First watchdog commit status=%h expected=3",status_value);

        repeat(140) @(negedge clk);
        read_word(16'h0054,2'b00,32'h00000007);
        read_word(16'h0060,2'b00,32'h00000001);
        repeat(40) @(negedge clk);
        read_word(16'h0060,2'b00,32'h00000001);

        send_write(16'h0050,32'h00000000,4'hf,0,2'b00);
        read_word(16'h0054,2'b00,32'h00000007);
        read_word(16'h0060,2'b00,32'h00000001);
        send_write(16'h0050,32'h00000001,4'hf,0,2'b00);
        read_word(16'h0054,2'b00,32'h00000003);
        read_register(16'h0064,watchdog_gap_value);
        if (watchdog_gap_value < 128)
            $fatal(1,"Watchdog recovery gap=%0d cycles",watchdog_gap_value);

        send_write(16'h004c,32'h00000000,4'hf,0,2'b00);
        read_word(16'h0054,2'b00,32'h00000000);
        read_word(16'h0060,2'b00,32'h00000001);
        send_write(16'h004c,32'h00000001,4'hf,0,2'b00);
        read_word(16'h0054,2'b00,32'h00000001);

        $display("PASS: AXI4-Lite register/core checks and monitor-only watchdog status path");
        $finish;
    end
endmodule
