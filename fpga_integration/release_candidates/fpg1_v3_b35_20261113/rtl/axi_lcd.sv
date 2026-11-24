module axi_lcd (
    input wire s_axi_aclk, input wire s_axi_aresetn,
    input wire [19:0] s_axi_awaddr, input wire [2:0] s_axi_awprot,
    input wire s_axi_awvalid, output wire s_axi_awready,
    input wire [31:0] s_axi_wdata, input wire [3:0] s_axi_wstrb,
    input wire s_axi_wvalid, output wire s_axi_wready,
    output reg [1:0] s_axi_bresp, output reg s_axi_bvalid, input wire s_axi_bready,
    input wire [19:0] s_axi_araddr, input wire [2:0] s_axi_arprot,
    input wire s_axi_arvalid, output wire s_axi_arready,
    output reg [31:0] s_axi_rdata, output reg [1:0] s_axi_rresp,
    output reg s_axi_rvalid, input wire s_axi_rready,
    output reg lcd_clk, output reg lcd_de,
    output wire lcd_hs, output wire lcd_vs, output wire lcd_bl, output wire lcd_rst,
    input wire [23:0] lcd_rgb_i,
    output wire [23:0] lcd_rgb_o, output wire [23:0] lcd_rgb_t
);
    localparam integer PAGE_WORDS = 48000;
    localparam [19:0] PAGE0 = 20'h01000, PAGE1 = 20'h31000;
    localparam [19:0] PAGE_BYTES = 20'd192000;
    (* ram_style = "block" *) reg [31:0] pixels [0:95999];
    reg [31:0] scan_word;
    reg [23:0] palette [0:15];
    reg aw_pending, w_pending;
    reg [19:0] aw_address;
    reg [31:0] w_data;
    reg [3:0] w_strobe;
    reg enable, has_frame, front_page, flip_pending, next_page;
    reg [31:0] generation, frame_count;
    reg [10:0] h_count, v_count;
    reg [15:0] startup;
    reg id_valid;
    (* ASYNC_REG = "TRUE" *) reg [2:0] id_meta, id_sync;
    reg [2:0] id_strap;
    reg [23:0] rgb_data;

    wire aw_fire = s_axi_awvalid && s_axi_awready;
    wire w_fire = s_axi_wvalid && s_axi_wready;
    wire [19:0] address = aw_pending ? aw_address : s_axi_awaddr;
    wire [31:0] data = w_pending ? w_data : s_axi_wdata;
    wire [3:0] strobe = w_pending ? w_strobe : s_axi_wstrb;
    wire commit = !s_axi_bvalid && (aw_pending || aw_fire) && (w_pending || w_fire);
    wire page0_write = address >= PAGE0 && address < PAGE0 + PAGE_BYTES;
    wire page1_write = address >= PAGE1 && address < PAGE1 + PAGE_BYTES;
    wire framebuffer_write = page0_write || page1_write;
    wire palette_write = address >= 20'h100 && address < 20'h140;
    wire aligned = address[1:0] == 0;
    wire can_write_pixels = aligned && framebuffer_write && !flip_pending &&
                            (!has_frame || (page1_write != front_page));
    wire [16:0] write_index = page1_write ? PAGE_WORDS + ((address - PAGE1) >> 2)
                                                        : (address - PAGE0) >> 2;
    wire active = id_valid && h_count >= 216 && h_count < 1016 &&
                             v_count >= 35 && v_count < 515;
    wire [10:0] x = h_count - 216;
    wire [10:0] y = v_count - 35;
    wire [16:0] scan_index = active ? (front_page ? PAGE_WORDS : 0) + y * 100 + (x >> 3) : 0;
    wire [3:0] color_index = scan_word >> (x[2:0] * 4);
    wire [15:0] lcd_id = id_strap == 3'b100 ? 16'h4384 :
                         id_strap == 3'b001 ? 16'h7084 : 16'h0000;
    wire supported = lcd_id != 0;
    wire frame_boundary = lcd_clk && h_count == 1055 && v_count == 524;
    assign s_axi_awready = !s_axi_bvalid && !aw_pending;
    assign s_axi_wready = !s_axi_bvalid && !w_pending;
    assign s_axi_arready = !s_axi_rvalid;
    assign lcd_hs = 1'b1;
    assign lcd_vs = 1'b1;
    assign lcd_rst = s_axi_aresetn;
    assign lcd_bl = s_axi_aresetn && id_valid && supported && enable;
    assign lcd_rgb_o = rgb_data;
    assign lcd_rgb_t = {24{!lcd_de}};

    // No RAM reset: the scanner uses generated color bars until the first complete page.
    integer lane;
    always @(posedge s_axi_aclk) begin
        if (s_axi_aresetn && commit && can_write_pixels)
            for (lane = 0; lane < 4; lane = lane + 1)
                if (strobe[lane]) pixels[write_index][lane*8 +: 8] <= data[lane*8 +: 8];
        scan_word <= pixels[scan_index];
    end

    integer i;
    reg good_write;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            aw_pending <= 0; w_pending <= 0;
            s_axi_bvalid <= 0; s_axi_bresp <= 0;
            s_axi_rvalid <= 0; s_axi_rdata <= 0; s_axi_rresp <= 0;
            enable <= 1; has_frame <= 0; front_page <= 0;
            flip_pending <= 0; next_page <= 0;
            generation <= 0; frame_count <= 0;
            lcd_clk <= 0; lcd_de <= 0; rgb_data <= 0;
            h_count <= 0; v_count <= 0;
            startup <= 0; id_valid <= 0;
            id_meta <= 0; id_sync <= 0; id_strap <= 0;
            for (i = 0; i < 16; i = i + 1) palette[i] <= 0;
        end else begin
            id_meta <= {lcd_rgb_i[7],lcd_rgb_i[15],lcd_rgb_i[23]};
            id_sync <= id_meta;
            if (!id_valid) begin
                startup <= startup + 1'b1;
                if (&startup) begin id_valid <= 1; id_strap <= id_sync; end
            end
            lcd_clk <= ~lcd_clk;
            // Update pins on PCLK falling edges; the panel samples the next rising edge.
            if (lcd_clk) begin
                lcd_de <= active && enable && supported;
                if (has_frame) rgb_data <= palette[color_index];
                else if (x < 160) rgb_data <= 24'hffffff;
                else if (x < 320) rgb_data <= 24'h000000;
                else if (x < 480) rgb_data <= 24'hff0000;
                else if (x < 640) rgb_data <= 24'h00ff00;
                else rgb_data <= 24'h0000ff;
                if (id_valid) begin
                    if (h_count == 1055) begin
                        h_count <= 0;
                        if (v_count == 524) v_count <= 0;
                        else v_count <= v_count + 1'b1;
                    end else h_count <= h_count + 1'b1;
                end
            end
            if (frame_boundary && id_valid) begin
                frame_count <= frame_count + 1'b1;
                if (flip_pending) begin
                    front_page <= next_page; has_frame <= 1;
                    flip_pending <= 0; generation <= generation + 1'b1;
                end
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (aw_fire) begin aw_pending <= 1; aw_address <= s_axi_awaddr; end
            if (w_fire) begin w_pending <= 1; w_data <= s_axi_wdata; w_strobe <= s_axi_wstrb; end
            if (commit) begin
                aw_pending <= 0; w_pending <= 0; s_axi_bvalid <= 1;
                good_write = 0;
                if (aligned) begin
                    if (framebuffer_write) good_write = can_write_pixels;
                    else if (palette_write && !has_frame && !flip_pending) begin
                        for (i = 0; i < 3; i = i + 1)
                            if (strobe[i]) palette[address[5:2]][i*8 +: 8] <= data[i*8 +: 8];
                        good_write = 1;
                    end else if (address == 20'h04 && (data & 32'hfffffffe) == 0) begin
                        if (strobe[0]) enable <= data[0];
                        good_write = 1;
                    end else if (address == 20'h10 && strobe == 4'hf &&
                                 data < 2 && data[0] != front_page && !flip_pending && enable) begin
                        next_page <= data[0]; flip_pending <= 1; good_write = 1;
                    end
                end
                s_axi_bresp <= good_write ? 2'b00 : 2'b10;
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1; s_axi_rresp <= 0;
                if (s_axi_araddr[1:0] != 0) begin s_axi_rresp <= 2; s_axi_rdata <= 0; end
                else if (s_axi_araddr >= 20'h100 && s_axi_araddr < 20'h140)
                    s_axi_rdata <= {8'd0,palette[s_axi_araddr[5:2]]};
                else case (s_axi_araddr)
                    20'h00: s_axi_rdata <= 32'h4c434431;
                    20'h04: s_axi_rdata <= {31'd0,enable};
                    20'h08: s_axi_rdata <= {27'd0,id_valid,enable,flip_pending,front_page,has_frame};
                    20'h0c: s_axi_rdata <= {16'd0,lcd_id};
                    20'h14: s_axi_rdata <= generation;
                    20'h18: s_axi_rdata <= frame_count;
                    20'h1c: s_axi_rdata <= {16'd480,16'd800};
                    20'h20: s_axi_rdata <= 50000000;
                    20'h24: s_axi_rdata <= 25000000;
                    20'h28: s_axi_rdata <= PAGE_BYTES;
                    default: begin s_axi_rdata <= 0; s_axi_rresp <= 2; end
                endcase
            end
        end
    end
endmodule
