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
    initial begin
        repeat(3) @(negedge clk);
        rst_n=1;
        read_word(12'h000,2'b00,32'h46504732);

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
