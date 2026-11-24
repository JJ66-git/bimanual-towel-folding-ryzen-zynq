`timescale 1ns/1ps
module tb_axi_lcd;
    reg clk = 0, reset_n = 0;
    always #10 clk = ~clk;
    reg [19:0] awaddr = 0, araddr = 0;
    reg awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
    reg [31:0] wdata = 0;
    reg [3:0] wstrb = 0;
    wire awready, wready, bvalid, arready, rvalid;
    wire [1:0] bresp, rresp;
    wire [31:0] rdata;
    wire lcd_clk, lcd_de, lcd_hs, lcd_vs, lcd_bl, lcd_rst;
    tri [23:0] lcd_rgb;
    wire [23:0] rgb_out, rgb_tri;
    genvar pin;
    generate for (pin=0;pin<24;pin=pin+1) begin: buffers
        assign lcd_rgb[pin] = rgb_tri[pin] ? 1'bz : rgb_out[pin];
    end endgenerate
    pullup(lcd_rgb[7]);
    pulldown(lcd_rgb[15]);
    pulldown(lcd_rgb[23]);
    axi_lcd dut(.s_axi_aclk(clk),.s_axi_aresetn(reset_n),
        .s_axi_awaddr(awaddr),.s_axi_awprot(3'd0),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
        .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arprot(3'd0),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
        .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
        .lcd_clk(lcd_clk),.lcd_de(lcd_de),.lcd_hs(lcd_hs),.lcd_vs(lcd_vs),
        .lcd_bl(lcd_bl),.lcd_rst(lcd_rst),
        .lcd_rgb_i(lcd_rgb),.lcd_rgb_o(rgb_out),.lcd_rgb_t(rgb_tri));

    task automatic write(input [19:0] addr,input [31:0] value,input [3:0] strobe,
                         input integer ordering,input [1:0] expected);
        integer j;
        begin
            if (ordering == 1) begin
                @(negedge clk); wdata=value; wstrb=strobe; wvalid=1;
                do @(posedge clk); while (!wready);
                @(negedge clk); wvalid=0;
                repeat(2) @(negedge clk);
            end
            @(negedge clk); awaddr=addr; awvalid=1;
            if (ordering == 0) begin wdata=value; wstrb=strobe; wvalid=1; end
            do @(posedge clk); while (!awready);
            @(negedge clk); awvalid=0;
            if (ordering == 0) wvalid=0;
            if (ordering == 2) begin
                repeat(2) @(negedge clk);
                wdata=value; wstrb=strobe; wvalid=1;
                do @(posedge clk); while (!wready);
                @(negedge clk); wvalid=0;
            end
            while (!bvalid) @(negedge clk);
            if (bresp !== expected) $fatal(1,"BRESP address %h expected %h got %h",addr,expected,bresp);
            if (ordering != 0) for (j=0;j<3;j=j+1) begin
                @(negedge clk);
                if (!bvalid || bresp !== expected || awready || wready)
                    $fatal(1,"write response was not held under backpressure");
            end
            bready=1; @(negedge clk); bready=0;
        end
    endtask

    task automatic read(input [19:0] addr,input [1:0] expected,output [31:0] value);
        integer j;
        begin
            @(negedge clk); araddr=addr; arvalid=1;
            do @(posedge clk); while (!arready);
            @(negedge clk); arvalid=0;
            while (!rvalid) @(negedge clk);
            value=rdata;
            if (rresp !== expected) $fatal(1,"RRESP at %h",addr);
            for(j=0;j<2;j=j+1) begin
                @(negedge clk);
                if (!rvalid || rdata !== value || rresp !== expected || arready)
                    $fatal(1,"read response was not held under backpressure");
            end
            rready=1; @(negedge clk); rready=0;
        end
    endtask

    integer px=0, py=0, observed=0, observed_second=0, mode=0, bars=0;
    reg was_de=0, complete=0, second_complete=0;
    reg [23:0] expected_rgb;
    integer color;
    always @(posedge lcd_clk) begin
        if (reset_n) begin
            if (lcd_hs !== 1 || lcd_vs !== 1 || lcd_rst !== 1)
                $fatal(1,"control pin polarity mismatch");
            if (lcd_de) begin
                if (!lcd_bl) $fatal(1,"active data with backlight disabled");
                if (mode == 0) begin
                    if (px<160) expected_rgb=24'hffffff;
                    else if (px<320) expected_rgb=24'h000000;
                    else if (px<480) expected_rgb=24'hff0000;
                    else if (px<640) expected_rgb=24'h00ff00;
                    else expected_rgb=24'h0000ff;
                    bars=bars+1;
                end else begin
                    color=(px/100+py/60+(mode==2 ? 3 : 0))%16;
                    if (mode==1 && py==0 && px<8) begin
                        color=px;
                        if (px==2 || px==3) color=10;
                    end
                    expected_rgb=24'h123456+color*24'h010101;
                    if (mode==1) observed=observed+1;
                    else observed_second=observed_second+1;
                end
                if (lcd_rgb !== expected_rgb)
                    $fatal(1,"pixel mode=%0d x=%0d y=%0d expected=%h got=%h",mode,px,py,expected_rgb,lcd_rgb);
                px=px+1;
                if (px>800) $fatal(1,"more than 800 pixels on a line");
            end else if (was_de) begin
                if (px!=800) $fatal(1,"incorrect active width %0d",px);
                px=0;
                if (mode!=0) begin
                    py=py+1;
                    if (py==480) begin
                        py=0;
                        if (mode==1) complete=1;
                        else second_complete=1;
                    end
                end
            end
            was_de=lcd_de;
        end
    end

    reg [31:0] value, word_value;
    integer i,n,row,column;
    initial begin
        repeat(8) @(negedge clk); reset_n=1;
        read(0,0,value);
        if (value!==32'h4c434431) $fatal(1,"bad BUILD_ID");
        read(20'h1c,0,value);
        if (value!=={16'd480,16'd800}) $fatal(1,"bad geometry");
        read(20'h03,2,value);
        read(20'h1000,2,value);
        write(20'h00,32'd1,15,1,2);
        write(20'h01001,32'd0,15,2,2);
        write(20'h30000,32'd0,15,0,2);
        for(i=0;i<16;i=i+1) begin
            write(20'h100+i*4,32'h123456+i*32'h010101,15,i%3,0);
            read(20'h100+i*4,0,value);
            if (value !== 32'h123456+i*32'h010101) $fatal(1,"palette readback mismatch");
        end
        // Full page through AXI, not hierarchical RAM initialization.
        for(i=0;i<48000;i=i+1) begin
            row=i/100; column=(i%100)*8; word_value=0;
            for(n=0;n<8;n=n+1)
                word_value=word_value | (((column+n)/100+row/60)%16 << (n*4));
            write(20'h31000+i*4,word_value,15,0,0);
        end
        write(20'h31000,32'h76543210,15,1,0);
        write(20'h31000,32'hffffaaff,2,2,0);
        write(20'h31000,32'hffffffff,0,0,0);
        read(20'h0c,0,value);
        if (value!==16'h4384) $fatal(1,"LCD strap decode mismatch");
        write(20'h10,0,15,0,2);
        write(20'h10,1,15,0,0);
        write(20'h31004,0,15,0,2);
        write(20'h01004,0,15,0,2);
        write(20'h10,1,15,0,2);
        value=0;
        while(value==0) read(20'h14,0,value);
        if (value!==1) $fatal(1,"unexpected flip generation");
        read(20'h08,0,value);
        if (value!==32'h1b) $fatal(1,"unexpected displayed page status %h",value);
        mode=1; px=0; py=0;
        write(20'h31000,0,15,0,2);
        write(20'h100,0,15,0,2);
        wait(complete);
        if (observed!=384000 || bars<800) $fatal(1,"incomplete native frame or color-bar coverage");
        for(i=0;i<48000;i=i+1) begin
            row=i/100; column=(i%100)*8; word_value=0;
            for(n=0;n<8;n=n+1)
                word_value=word_value | (((column+n)/100+row/60+3)%16 << (n*4));
            write(20'h01000+i*4,word_value,15,0,0);
        end
        write(20'h10,0,15,0,0);
        value=1;
        while(value==1) read(20'h14,0,value);
        if(value!==2) $fatal(1,"second page was not displayed");
        mode=2; px=0; py=0;
        read(20'h08,0,value);
        if(value!==32'h19) $fatal(1,"page0 status mismatch");
        wait(second_complete);
        if(observed_second!=384000) $fatal(1,"second page pixel count mismatch");
        write(20'h04,0,15,0,0);
        repeat(8) @(posedge lcd_clk);
        if (lcd_de || lcd_bl) $fatal(1,"disable failed");
        @(negedge clk); reset_n=0;
        repeat(3) @(posedge clk);
        if (lcd_rst) $fatal(1,"reset pin failed");
        $display("PASS: LCD AXI, palette, color bars, 768000 pixels and two vblank flips");
        $finish;
    end
    initial begin #200000000; $fatal(1,"LCD testbench timeout"); end
endmodule
