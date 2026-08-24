---
name: converting-easyeda-pro-to-kicad
description: Use when converting a 嘉立创EDA专业版 / EasyEDA Pro / JLCEDA project (.eprj2 / .epro / .epro2) to KiCad, when an imported EasyEDA project fails ERC/DRC/schematic parity (pin_not_driven flood, net_conflict $1Nxxxx, footprint_link_issues), when its 3D view shows no components (missing EASYEDA_MODELS/*.step), or when making an imported project iteration-ready (zone refill causes unconnected pads / starved_thermal, field sync direction unclear, empty footprint Value never matches parity, off-grid pins)
---

# EasyEDA Pro → KiCad 10 转换

## Overview

立创专业版工程转 KiCad 的完整管线:选对源文件 → GUI 导入 → 证明网表等价并修复导入损伤 → 元数据纠错 → 规则移植 → 补 3D → ERC/DRC 全零;若要继续迭代,再做规范化。核心原则:**先证明「KiCad 网表 ≡ 源网表」再谈其他**——导入器会以 −0.0254mm 级平移打断原理图连线,不核对就是静默错板。第二原则:**冻结几何是源意图的最终裁判**,与规则记录矛盾时以几何量测为准。

## When to Use

- 手头是 `.eprj2` / `.epro` / `.epro2` 要转 KiCad
- 导入后 ERC/DRC/parity 大面积报错、3D 视图空白
- 转换完成后要把工程变成可持续迭代的正常 KiCad 工程
- 不适用:EasyEDA 标准版(走 easyeda2kicad 即可)、只拉单个器件库

## 格式速查

| 文件 | 本质 | 用途 |
|---|---|---|
| `.eprj2` | SQLite;工程在 history_data 表 = base 快照+增量,base64 **加密**不可读 | 不能直接转 |
| `.epro`(菜单导出) | zip:`SHEET/<uuid>/1.esch` + `PCB/*.epcb` + SYMBOL/FOOTPRINT | **KiCad 唯一能吃的格式**;3D 壳体不随导出(INSTANCE/ 为空) |
| `_backup/*.epro2` | zip,内含 `.epru` = **明文全量 JSONL**(DOCHEAD 分段,带 updateTime/ticket) | 判断 .epro 是否过期、语义 diff 的真值源 |

`.epru` 坐标 y 与 `.esch` 符号相反;RULE 记录数值**单位是 mil**(虽标 mm,mil→mm 为 ×0.0254);ticket/firstTicket 可定位每次编辑改了什么。

## Pipeline(转换,目标:忠实 + 双零)

1. **源新鲜度**:比对 `.epro` 与最新 `.epru` 的 DOCHEAD updateTime。有分歧就对分歧文档做语义 diff(COMPONENT/WIRE/ATTR 集合比对),电气等价才可用旧 `.epro`,否则去 GUI 重导出。
2. **导入必须走 KiCad GUI**(kicad-cli 无 EasyEDA 导入;`pcb import` 只支持 altium/eagle 等):`文件 → 导入非 KiCad 工程 → EasyEDA (JLCEDA) 专业版工程`。macOS osascript 自动化可行:进程名 `kicad`(pgrep **-x**);NSOpenPanel 先 AXRaise+frontmost 再 Cmd+Shift+G,往 sheet 的 text field `set value`;**导入后 sch/pcb 只在内存,必须各窗口 Cmd+S 落盘**再退出。
3. **网表等价核对(咽喉)**:`kicad-cli sch export netlist` 得 sch 侧 (ref,pad)→net;解析 .kicad_pcb 得 pcb 侧(pcb 焊盘网名 = EasyEDA 原始网表直通,是真值)。做**分区同构比对**(忽略网名看分组),脚本见 `scripts/t1_compare.py`。分歧逐个归因修复,证据必须来自源 `.esch`/`.epru`。
4. **修导入损伤**(每处带源证据,只做最小动作):线端点吸附、丢失 junction 补回、ref 关联修复。源本来就悬空的**不修**,列清单走 severity。
5. **pcb 网名统一**:分区同构后把 `$1Nxxxx` 批量改成 sch 自动名。注意:引脚名 `/`→`{slash}`;根页本地标签网前缀 `/`;nets 声明与 pad/via/track/zone 引用全改。
6. **元数据纠错**:引脚 input/unspecified→passive(sch 内嵌 lib_symbols 与 .kicad_sym **同步改**,否则 lib_symbol_mismatch);pcbnew python(KiCad 10 是 `FindPlugin` 非 `PluginFind`)FootprintSave 抽 .pretty + 写 fp-lib-table;封装名含 `*` 要统一改名(库按文件名登记,`*` 不能做文件名)。
7. **规则移植**:源 RULE(mil!)+ 目标工艺能力写入 .kicad_pro;netclass clearance 用源值。
8. **3D 补齐**:见下节。
9. **终验**:`erc --exit-code-violations` + `drc --schematic-parity --exit-code-violations` 双 exit 0;重跑分区比对证明后续修复没动网表。

## 3D 壳体补拉

导入器只写引用 `${KIPRJMOD}/EASYEDA_MODELS/<3D Model Title>.step`(offset 已烘进 pcb),文件要自己拿。端点链(`scripts/fetch.py`,路径需改):

1. `POST pro.easyeda.com/api/v2/devices/searchByCodes`(data `codes[]=C编号`;**必须带** `Referer: https://pro.easyeda.com/editor` + `X-Requested-With: XMLHttpRequest`,否则 403)→ 云端 device 的 3D Model uuid。本地 project.json 里的 uuid 是本地快照 id,云端查不到,必须经 C 编号换。
2. `GET pro.easyeda.com/api/v2/components/{uuid}` → `dataStr.model` = 直接 uuid;dataStr 加密时用响应里的 `3d_model_uuid`。
3. `GET modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{直接uuid}` → 原始 STEP。
4. Pro 查不到的(非公开壳体)走标准版 `easyeda.com/api/products/{C编号}/components?version=6.4.19.5` → SVGNODE `attrs.uuid` 直接喂第 3 步。
5. 归一化(`scripts/normalize.py`):KiCad 自带 python 的 `pcbnew.UTILS_STEP_MODEL`,包围盒 XY 居中、Z 底面归零,SaveSTEP。**不必**走 easyeda2kicad+wrl 烘焙。
6. 验收:`pcb export step --subst-models` 零缺模告警 + `pcb render` 出图**目检**器件立在板上。

## 转为迭代主版本(规范化)

先分用途:**只归档查阅** → 上面管线到双零为止,冻结几何 + 窄化豁免就是正解;**要继续迭代** → 必须做本节,否则第一次编辑就触雷(off-grid 引脚接不上新线、zone 一动就全板重灌出一片失连)。每步硬门:ERC/DRC 双零 + 网表分区与基线逐一相同。

1. **库改名**脱离导入器起的版本化名字、3D 迁 `<lib>.3dshapes/` 惯例:改 sch 嵌入定义 + lib_id + Footprint 属性、pcb FPID、两张库表、.pretty 内 model 路径,每处替换双向 grep 计数核对归零。
2. **网格吸附**:off-grid 逐个归因——实例级(符号整体平移 ±0.0254 之类,移回 1.27mm 格、连线端点同步)vs 定义级(引脚在符号定义内就不在格上,动库另议)。陷阱:**no_connect 标记不随符号平移**,要单独同步,否则爆 pin_not_connected;kicad-cli ERC JSON 的 pos 是**锚点不是违规点**、值 ×100 = mm。
3. **悬空线头**:label 锚在待删线段上的要**截短到锚点**而非整删,整删会悬空 label 改网名。
4. **PWR_FLAG 与 #PWR 批注**可纯文本完成:property "Reference" 与 instances 区双改,ref 全局唯一;做完 `sch export netlist` 的「批注错误」警告应消失。
5. **字段同步方向看数据不看报告**:parity 报「原理图缺少符号字段」时,实测常是 **sch 侧字段齐全、pcb 封装侧全空** → 方向 sch→pcb(与报告表面含义相反)。空 Value 封装陷阱:KiCad 加载时用位号顶替封装的空 Value,parity 空对空**永不匹配**——sch 侧补隐藏 Value(可取封装名)双侧同步。
6. **重灌铜**(最险,见下节)。
7. 收敛后删 `.kicad_dru` 豁免、恢复 solder_mask_bridge 等被 ignore 的检查,终验四门(ERC/DRC/netlist 分区/render 目检)。

## 重灌铜(冻结填充 → KiCad 引擎)

- 先分类导入 zone:**priority 600 的一大批是泪滴**(pcbnew `IsTeardropArea()` 鉴别),不是填充区;另有少量真铺铜 + keepout。
- **pad_connection 全改 solid 再灌**。源 RULE 写 DIVERGENCE(thermal)也别信——判据是**冻结几何逐焊盘覆盖率量测**(EasyEDA 导出几何通常实为直连)。带 thermal 直灌的必然后果:小铜皮塌缩成孤岛被删、焊盘/泪滴失连(unconnected_items)、starved_thermal 满天飞。
- `min_resolved_spokes=1`(直连无辐条)。
- **验收定量化**:DRC unconnected=0 + 逐网面积表 vs 冻结基线 ±10%。KiCad 引擎同规则下比 EasyEDA 流得更满,GND +几% 属正常纯增益;泪滴 zone 面积应逐一不变。
- zone priority 看似乱序别急着重排:先确认涉事 zone **同层且真重叠**——不同层的"重叠"是假象,重排是无效操作。
- 灌后 Gerber ≠ ODM 原版,这是迭代分叉点,记入板卡 README。

## Common Mistakes

| 症状/想法 | 真相 |
|---|---|
| 直接解析 .eprj2 | 数据加密,死路;用 .epro + .epru 备份 |
| pin_not_driven/pin_to_pin 几百条,想加 PWR_FLAG 或逐条豁免 | 根因是导入器把引脚全标 input/unspecified,批量改 passive 即消 |
| net_conflict 上百条,想 severity ignore | 两种模式混在一起:自动网名体系差异(改名可消)+ **真实断连**(必须修);先分区比对再动手 |
| endpoint_off_grid 是提示级,先忽略 | 它正是断连信号,网表等价证明之前不许 ignore |
| DRC 逐条 exclusion 走 CLI | 不可铸造(marker 坐标=碰撞接触点,只有 DRC 引擎算得出);用 severity 或 .kicad_dru 网对窄化规则 |
| 冻结 zone 贴异网焊盘 0.0mm | 归档场景:.kicad_dru 网对豁免,不动铜;**迭代场景:按「重灌铜」节全 solid 重灌**,别带着冻结铜迭代 |
| 重灌后失连,调 zone thermal 参数硬闯 | 根因多半是 pad_connection 该 solid 而非 thermal;参数微调治不了语义错置 |
| footprint_symbol_mismatch 消不掉的空 Value 封装 | KiCad 用位号顶替空封装 Value,空对空永不匹配;sch 补隐藏 Value 才是修法 |
| 拿 LCSC 原始 STEP 直接用 | 画布坐标系会飞出板外;按上面链路取 Pro 壳体+归一化 |

## scripts/

- `t1_compare.py` 网表分区同构比对(改文件路径后用) · `schtool.py` sch s-expr 工具
- `fetch.py` 3D 壳体端点链下载 · `normalize.py` STEP 归一化(需 KiCad 自带 python)
