---
name: FlexiFold Cooperative Monitor
description: Warm, result-led visual monitoring of actual AI PC, PS, PL and display cooperation.
colors:
  paper: '#f2eee2'
  ink: '#191c18'
  accent: '#e94c2c'
  acid: '#c2dc63'
  muted: '#69695d'
  line: '#d8d2c1'
  white: '#fffaf0'
  olive: '#526a2d'
  sand: '#e9deb3'
  red: '#b52e20'
  sand-text: '#57533b'
  lcd-line: '#e0dac9'
  lcd-muted: '#5c5d51'
  lcd-white: '#fffaee'
  lcd-dark: '#373b33'
  lcd-green: '#a4ae87'
  lcd-red: '#bc3422'
  lcd-shade: '#cec7b5'
typography:
  display:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: clamp(34px, 3.3vw, 58px)
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: -0.035em
  headline:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: 40px
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: -0.035em
  body:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: 15px
    fontWeight: 400
  body-support:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.8
  verdict:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: 24px
    fontWeight: 700
  section:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: 20px
    fontWeight: 700
    lineHeight: 1.4
  metric:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: 27px
    fontWeight: 400
    letterSpacing: -0.03em
  label:
    fontFamily: DisplaySC, 'Noto Sans CJK SC', sans-serif
    fontSize: 14px
    fontWeight: 400
  lcd-display:
    fontFamily: DisplaySansSC-Bold.otf
    fontSize: 44px
    fontWeight: 700
  lcd-node-title:
    fontFamily: DisplaySansSC-Bold.otf
    fontSize: 24px
    fontWeight: 700
  lcd-node-role:
    fontFamily: DisplaySansSC.otf
    fontSize: 20px
    fontWeight: 400
  lcd-verdict:
    fontFamily: DisplaySansSC-Bold.otf
    fontSize: 28px
    fontWeight: 700
  lcd-age:
    fontFamily: DisplaySansSC-Bold.otf
    fontSize: 34px
    fontWeight: 700
  lcd-label:
    fontFamily: DisplaySansSC.otf
    fontSize: 18px
    fontWeight: 400
  lcd-phase-index:
    fontFamily: DisplaySansSC.otf
    fontSize: 18px
    fontWeight: 400
  lcd-footer:
    fontFamily: DisplaySansSC.otf
    fontSize: 14px
    fontWeight: 400
rounded:
  tag: 2px
  tab: 3px
  control: 6px
  chart: 8px
  evidence: 12px
  device: 20px
  lcd-status: 10px
  lcd-node: 5px
spacing:
  control-gap: 8px
  compact-gap: 12px
  section-gap: 24px
  hero-gap: 32px
  work-gap: 36px
  shell-inset: 42px
  lcd-edge: 24px
  lcd-box-inset: 16px
components:
  button-utility:
    textColor: '{colors.ink}'
    rounded: '{rounded.control}'
    padding: 8px 13px
  button-dark:
    backgroundColor: '{colors.ink}'
    textColor: '{colors.paper}'
    rounded: '{rounded.control}'
    padding: 8px 13px
  nav-selected:
    backgroundColor: '{colors.ink}'
    textColor: '{colors.paper}'
    rounded: '{rounded.tab}'
    padding: 10px 17px
  source-host:
    backgroundColor: '{colors.ink}'
    textColor: '{colors.paper}'
    rounded: '{rounded.tag}'
    padding: 5px 9px
  source-synthetic:
    backgroundColor: '{colors.accent}'
    textColor: '{colors.ink}'
    rounded: '{rounded.tag}'
    padding: 5px 9px
  evidence-card:
    backgroundColor: '{colors.sand}'
    textColor: '{colors.ink}'
    rounded: '{rounded.evidence}'
    padding: 23px 25px 14px
  lcd-status:
    backgroundColor: '{colors.ink}'
    textColor: '{colors.lcd-white}'
    rounded: '{rounded.lcd-status}'
    width: 432px
    height: 160px
  lcd-constraint:
    backgroundColor: '{colors.lcd-white}'
    textColor: '{colors.ink}'
    rounded: '{rounded.control}'
    width: 278px
    height: 90px
---

# Design System: FlexiFold Cooperative Monitor

## Overview

**Creative North Star: "The Result-Led Cooperation Monitor"**

Warm paper, charcoal, vermilion and acid green frame a compact industrial monitor. A Chinese state outcome leads; authored FPGA pins, wires and phase markers explain cooperation without competing with the result. Large light text on the LCD's charcoal status panel follows the user's physical-screen feedback.

The system preserves essential measured values and makes their source and age visible. The browser expands evidence and records; the portrait LCD concentrates the same observation into judge-readable outcomes. Its tone is precise and restrained, with no AI-purple or AI-blue identity. The visual metaphor names the implemented direction; it is not an approved concept comp or a claimed user pin.

**Key Characteristics:**
- Result-first Chinese judgments with essential measurements retained.
- Warm flat surfaces with authored chip and wire geometry.
- Source, freshness, correlated transaction number, current phase and historical state remain explicit.
- Read-only companion controls support inspection and evidence export.

This is an extraction from the finished implementation, not a seed. Sources are `style.css`, `index.html`, `app.js`, the LCD renderer, and `docs/FPGA_VISUAL_DIRECTION_zh.md`. The renderer's repository path is `fpga_integration/fpga_code/fpga_workspace/screen/cooperative_view.py`. `PRODUCT.md` supplies the confirmed durable user constraints. Context loading was refused; direct source reading replaced it. Concept seeding and approved-composition selection were not run.

## Colors

A warm neutral foundation carries two sharp functional accents; warning red remains a semantic result color. Frontmatter values are normative. The browser's custom-property names are retained. LCD-specific names distinguish actual framebuffer palette differences rather than silently equating them with CSS colors.

### Primary

- **Vermilion (`accent`)** marks a phase eligible to be shown as current, source warnings, selected data activity and focus. Synthetic badges use dark text after the final contrast refinement.

### Secondary

- **Acid green (`acid`)** marks reached phases, observed activity, stable evidence and the pressed freeze control. It does not independently assert task success.
- **Olive (`olive`)** is the browser connection dot and non-alert constraint icon.
- **Warning red (`red`, `lcd-red`)** identifies input violations and insufficient evidence on light surfaces. Browser errors use warning red with warm white text.

### Neutral

- **Warm paper (`paper`)** is the page and logical LCD canvas; **charcoal (`ink`)** is the main text, rail, chip, selected tab and LCD status surface.
- **Warm white (`white`, `lcd-white`)** supports light cards and dark-surface text. The framebuffer white differs slightly from browser white.
- **Muted ink (`muted`, `lcd-muted`)** supports secondary labels. LCD muted ink is a separate, darker palette entry.
- **Divider (`line`, `lcd-line`)** separates sections and forms inactive stage tracks. The LCD divider is a distinct color.
- **Sand (`sand`)** groups visual evidence. **Sand support ink (`sand-text`)** is the final browser override for its heading, description, footnote, unfilled evidence digits and stable-frame label; do not restore the weaker generic muted treatment there.
- **LCD dark, pin green and shade (`lcd-dark`, `lcd-green`, `lcd-shade`)** express unlit chip cells, package pins, old current-phase markers and unfilled evidence cells. The renderer declares a 16-color palette; unused amber, olive, pure-white and black entries are not promoted into reusable tokens here.

**The Status Contrast Rule.** Use light text on the charcoal status surface; use vermilion as a marker, not as small status text on black.

The sidecar's eight-step tonal strips are synthesized previews, not additional shipping colors or approved semantic state ramps.

## Typography

**Display and body font:** local `DisplaySC` regular and bold WOFF2 subsets, with `Noto Sans CJK SC`, then `sans-serif` fallback. Browser assets are `assets/DisplaySansSC.woff2` and `assets/DisplaySansSC-Bold.woff2`; their `font-display` is `swap`. The LCD uses adjacent `screen/assets/DisplaySansSC.otf` and `DisplaySansSC-Bold.otf`, then Noto CJK TTC index 2, then DejaVu Sans as runtime fallbacks. `assets/FONT_LICENSE.txt` records Noto CJK provenance and the SIL Open Font License; retain the bundled license with redistribution.

The type is direct Chinese sans serif with compact technical details. Font filenames in the LCD frontmatter roles identify Pillow assets, not browser CSS families. Pillow selects the bold asset at sizes of at least 22px; smaller roles use regular. Pillow positions raster glyph masks, so no invented CSS line-height is assigned to LCD roles.

### Hierarchy

| Role | Implemented use |
| --- | --- |
| Browser display | Main judgment uses the frontmatter clamp; at 950px and below it becomes 44px, then 38px at 600px and below. |
| Browser headline | Evidence/LCD page titles use 40px, then 32px at the narrow breakpoint. |
| Browser verdict / section | Decision verdicts use 24px (25px narrow); section headings use 20px, with smaller operational subheads. |
| Browser body / metric | Body is 15px; judgment support has 1.8 line-height and 35em maximum width. Metrics use tabular numerals at 27px, 24px at the medium breakpoint, 29px narrow. |
| Browser labels | Navigation is 14px; source labels are 12px (11px narrow). Dense evidence and provenance details vary from 10px to 13px; these are browser-only sizes. |
| LCD judgment | Starts at 44px and reduces one pixel at a time to fit a 296px mask width. Current headline vocabulary lands around 40–44px; do not assume arbitrarily long text will stay in that range. |
| LCD cooperation boxes | Node title 24px bold; dynamic transaction status 20px regular. |
| LCD verdict / age | Constraint and evidence outcomes 28px bold; feedback age 34px bold. |
| LCD labels / phase | Boxed labels and support 18px regular; current phase label 22px bold, other phase labels 18px; phase and evidence index digits 18px. |
| LCD measures / footer | Essential measure values 30px bold; release identity and clock footer 14px. |

**The Box Legibility Rule.** Keep the LCD boxed label, verdict and feedback-age roles at their established enlarged sizes, and preserve an inset between every glyph and its frame.

## Layout

The desktop browser has an 82px fixed rail, a shell capped at 1700px with 42px horizontal inset, a 94px header and a two-column judgment/cooperation hero. The hero uses `minmax(290px,.9fr)` and `minmax(520px,1.4fr)` columns with a 32px gap; operational content pairs a flexible main column with a 282px event column separated by 36px. Evidence uses a flexible record area and a 290px identity panel. The LCD route pairs a 450px device with explanation text and an 80px gap. Tables and event histories scroll inside bounded regions; 14 vector values occupy seven columns at full width.

| Breakpoint | Actual behavior |
| --- | --- |
| At least 1600px | Hero gap grows to 60px; event column becomes 310px. |
| At most 1200px | Shell inset becomes 28px, project note and wall clock hide, hero columns tighten, event column becomes 240px, stage connectors hide and stage circles shrink. LCD route uses two narrower columns. |
| At most 950px | Rail becomes 62px, shell inset 24px. Hero, work area, evidence and LCD routes stack. Events and platform details share two columns; vectors use four columns. |
| At most 600px | Rail hides, shell inset is 19px, header is 78px. Tabs span a wrapped toolbar, results and event columns stack, metrics and vectors use two columns, footer stacks. LCD device padding reduces from 15px to 11px and radius from 20px to 17px. Cooperation diagram height is 280px after the final explanatory-text override. |

The native 800×480 4.3-inch panel is mounted portrait. Its authored logical canvas is exactly 480×800; it is not a responsive rearrangement of the web page. Outer content runs from x=24 to x=456. Keep 24px canvas-edge insets and generally 16px inner card insets, with the primary headline at x=41 and status line at x=56.

| LCD region | Logical bounds / arrangement |
| --- | --- |
| Brand | Top separator at y=76. |
| Main status | (24,91)–(456,251), charcoal with light outcome and support. |
| Cooperation | Dynamic summary at y=269; three 120×66px boxes at y=298, x=24/180/336; 36px gaps include drawn arrows. |
| Phase | Heading near y=377; stage centers x=80/240/400, y=419; current/last badge bounds (323,371)–(456,400). |
| Constraint / age | (24,477)–(302,567) and (314,477)–(456,567); 12px separation. |
| Evidence | (24,583)–(456,691), sand surface. |
| Essential measures | Three columns start at x=24/175/326; labels y=706 and values y=730. |
| Footer | Separator at y=762; release/clock at y=773. |

These bounds describe the current canvas, not a claim that a new string is safe. Renderer `maxw` trims text by removing trailing characters; preserve concise labels and inspect any changed vocabulary for truncation and glyph-to-frame clearance.

## Elevation & Depth

Flat tonal layering and fine dividers carry most hierarchy. The browser's chip, LCD device frame and transient toast have soft shadows; the physical LCD uses no shadow vocabulary. The chip has a clipped upper-right package corner, and drawn pins/wires provide component structure. Browser pins belong to a positioned, isolated package wrapper with 12px top/bottom and −9px side insets. They surround the chip only; dynamic FPGA explanation text sits outside that wrapper with 15px top clearance.

- **Chip:** `0 12px 25px #252a1b1a` (the clip path can also clip its visible shadow).
- **LCD preview device:** `0 18px 40px #1b201324`.
- **Toast:** `0 8px 25px #0002`.

Keep shadows local to these existing supporting surfaces. Do not add depth to measured values or imply that visual elevation is a stronger result.

## Shapes

The form language is rectangular and measured: small tag and tab corners, modest control/card rounding, a softened device frame, thin rules and circular stage markers. Frontmatter records reused radii; the LCD verdict uses a 10px radius, cooperation nodes 5px, and constraint/age/evidence boxes 6px. Browser stage circles are 44px, 35px at the medium breakpoint and 30px narrow. LCD phase circles are 28px across. LCD phase digits are centered at each circle center using the actual alpha-mask ink bounding box, rather than a fixed baseline offset. Evidence digits use the same cropped-ink centering inside their boxes. The authored chip silhouette has a 16px clipped corner. Icons are outlined SVGs or Pillow geometry rather than decorative glyphs.

## Components

### Buttons and navigation

Browser utility buttons are unfilled with 8×13px padding and at least 42px height. Hover uses a warm darker paper; active scales to .96; disabled lowers opacity to .5 and uses a wait cursor. Copy and LCD enlargement use charcoal with warm paper text. The final selected-tab rule keeps charcoal/paper through hover; tab transitions affect transform only. Tabs have selected/controlled-panel semantics, roving tab stops and Arrow/Home/End navigation. All keyboard focus uses a 2px vermilion outline with 5px offset. Freeze has explicit pressed state and acid fill; its label becomes “恢复实时”. There are no editable input fields in this read-only companion, so none are invented in the sidecar.

### Source tags and notices

Host/source tags are charcoal with light text; synthetic tags are vermilion with dark text. A full-width synthetic notice repeats that vectors are synthetic and PL results do not represent a physical robot task. Render-only acceptance fixtures have their own explicit notice. Ordinary stale/frozen notices use sand; transport failures use red with warm white text. Status wording and source tags remain visible alongside color.

### FPGA cooperation diagram

The diagram explains one actual transaction: action data reached the board, FPGA checked it and sent its reply, then the board/result view presents that result. `snapshot.cooperation` supplies the summary, three dynamic statuses and their explanatory proof text. When a real sequence exists, the LCD summary reads “电脑第N条：FPGA已检查并回包”; browser host, FPGA and display proof text all correlate to that same received sequence. This is evidence of the board-side reply, not proof that the control PC received it.

Without an action sequence, the LCD statuses remain “等待动作” (or “连接已通”), “准备检查” and “等待结果”. A connection probe may establish connectivity but cannot create an action transaction. Fresh statuses become “动作已送达”, “已经检查” (or “发现问题”) and “板屏已显示”. When stale, the LCD keeps “等待更新”, “上次结果” and “保留结果”, while the summary and proofs identify the last transaction. Browser display text separately reports the current viewing state, and freezing changes it to “本页已冻结” with explicit wording that the board continues updating.

The browser uses an 8×4 cell chip grid and a packet-sequence-positioned wire dash. Only fresh connected snapshots expose the transaction dash. Chip cells require `phase_current` and use the actual phase and violation mask. This separates observed packet flow from eligibility to call a phase current. The LCD uses a 3×3 chip grid, package pins and sample-sequence dots. These are event-driven observations, not free-running decorative simulations. Browser host/display proofs use line-height 1.6, a 160px maximum width and 38px minimum height; the FPGA proof uses line-height 1.65 and 32px minimum height. On narrow screens these become 84px/43px for host/display proofs and 46px minimum height for the FPGA proof, keeping the explanation readable rather than forcing one line.

**The Transaction Correlation Rule.** Tie received action, FPGA check/reply and displayed result to the same actual sequence; do not imply that the control PC acknowledged receipt of the reply.

### Phase track

The browser shows reached phases as charcoal/acid circles and a valid current phase as vermilion/charcoal with a bold label. `phase_current` requires all three conditions: task sample age at most 200ms, the PL phase-valid reply bit (`phase_sample_valid`, from `reply.values[6] & 4`), and a known `feedback_age_us` no greater than 100000μs. A recent packet alone is insufficient. If any condition fails, a retained nonzero phase reads “上次”, the browser circle uses divider fill and the LCD circle uses shade; phase zero remains “等待识别”. LCD reached phases retain charcoal circles with acid checkmarks. Chip cells also use this validity gate. Cooperation status and transaction-flow markers can still report actual received transactions when phase eligibility fails. The PL phase is independent of the host control-step progress.

### Constraint and evidence results

Plain-language LCD section labels are “动作数据检查”, “发出时距更新” and “折叠结果检查”. The underlying global 14-dimensional input outcome remains a verdict, not per-channel approval or motion authorization. Waiting reads “等待动作数据”; a fresh clean check reads “未发现数据异常”, and historical labels explicitly retain “上次”. Browser normal and alert outlines are olive and red; LCD violations are red on light cards.

The evidence card uses sand, a textual verdict, check-round/frame provenance and three stable-frame indicators. Its four result labels are “等待双臂退开”, “正在检查折叠”, “已观察到折叠” and “暂时无法确认”. Supporting copy explains that the arms first return to the observation position before checking new towel images. Browser support text uses sand support ink. Unknown calibration cannot impersonate field validation; the LCD states “相机检查还需现场标定” when appropriate, and synthetic evidence stays labeled “合成画面测试，非现场结果”.

### Metrics, history and LCD mirror

LCD essential measures use “本轮检查次数”, “FPGA用时” and “整板响应用时”; the browser expands these to “本轮动作检查次数” and “FPGA计算用时”. The LCD label “发出时距更新” and browser label “动作发出时距状态更新” both display `request.feedback_age_us` (the accepted request field stored as `feedback_age_us`). This is how long the supplied robot state had gone without an update when the computer sent that action. It is a recorded per-action age, not time elapsed until the present display moment. Stopped input preserves that same record; the value must not tick forward with the page clock. Browser support text states “电脑发出这条动作时，状态距上次更新的时间；停更后保留该次记录”. “整板响应用时” is PS service time and excludes the computer-to-board network round trip. Unknown metrics show `--`; absent feedback does not imply freshness. Tables, charts and recent events derive only from collected history. CPU/environment readiness describes known environment state, not unmeasured inference utilization. The LCD tab requests actual submitted board frames and preserves their 480:800 aspect ratio. Freeze stops displayed updates while acquisition continues; export/copy serialize the currently rendered snapshot, and fullscreen only enlarges the LCD image. These controls send no task or robot packets.

| Observation state | Presentation contract |
| --- | --- |
| No valid task | “等待任务开始” / “等待动作数据”; unknown measures and empty history are intentional. A probe or host declaration does not establish a task check. |
| Fresh task | Sample age is at most 200ms in the board state model. Actual received transactions may update cooperation, mask, evidence and measurements; showing a phase as current additionally requires the PL valid bit and known feedback age at most 100000μs. |
| Fresh transaction, invalid phase input | Keep transaction cooperation truthful, but show “等待有效状态” / “这条状态未用于判断阶段” when no input violation takes precedence. Retained phase labels read “上次” and current-phase fill is removed. |
| Historical task | “等待新数据” with “下方为上次结果，等待新的动作数据” retains the last conclusion and phase while stopping live activity. The final browser override keeps the chip opaque rather than fading away its legibility. |
| Input violation | “动作数据有异常”; explicit mask/reasons and warning treatment. |
| Transport disconnected | Browser requires a connected transport younger than 2500ms; otherwise it shows a connection failure, retains the last snapshot and dims the flow to .45. The board's separate host-connected threshold is 5000ms. |
| Recorded action-send age unknown | Show unknown, keep `phase_current` false, and do not infer freshness from display refresh or packet arrival. A known value remains the age recorded at action-send time. |
| Synthetic / replay | Preserve “合成测试” / “记录回放” source identity. Synthetic banners distinguish actual PL-backed synthetic input from render-only fixtures. |

**The Evidence Before Activity Rule.** Update phase highlights, chip cells, wires and charts only from received task and PL state; waiting and stale views must not generate simulated activity.

**The Historical Phase Rule.** Call a phase current only with a fresh task sample, a PL phase-valid result and known recorded feedback age at most 100000μs; otherwise preserve it as the last phase and remove the live vermilion fill, independently of actual transaction flow.

Motion is brief and functional: 130ms ease-out controls/cells, 150ms wire/progress/evidence updates and 120ms wire-opacity changes. The declared `--ease` curve is not consumed by current component rules and is not promoted as a live motion standard. `prefers-reduced-motion: reduce` disables transitions and animations. Polling at 500ms is data acquisition, not an animation clock.

Captures and provenance are in `/home/ysyx/FPGA_competition/local_environment_record/fpga_visual_ui_20261113`: `final_live_sequence` PNGs are actual submitted framebuffers; the recorded SYNTHETIC GIFs show labeled synthetic UDP/PL sequences; `browser` contains browser evidence. These are verification artifacts rather than stock or generative imagery. No raster stock/generative assets ship with the interface; its visual geometry is code-authored. The supplied finish review accepted typography/padding and browser support/selected-tab contrast refinements. Actual camera/robot field validation and final human panel-eye legibility after enlargement remain unverified; neither is a design-system success claim.

## Do's and Don'ts

### Do:
- Do lead with plain Chinese state outcomes, then source, age, correlated transaction number, phase and essential measurements.
- Do preserve the enlarged LCD type roles and the balanced box insets.
- Do keep unknown values, stale results, host claims, probes and synthetic input visibly distinct.
- Do pair the current phase marker with literal current-phase text and preserve keyboard-visible focus.
- Do use actual packet history for charts and actual submitted framebuffers for the LCD mirror.

### Don't:
- Don't introduce the rejected AI-purple or AI-blue visual identity.
- Don't render small red status text on the charcoal LCD panel.
- Don't describe a global input check as permission to move a robot or an uncalibrated visual result as field success.
- Don't animate pretend progress, invent utilization data or disguise synthetic captures as a live robot task.
- Don't treat a framebuffer capture as a photograph or final proof of physical panel legibility.
