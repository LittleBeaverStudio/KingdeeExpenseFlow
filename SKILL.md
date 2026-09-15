---
name: kingdee-expense-flow
description: 金蝶云星空「费用/差旅报销全流程」提交 Skill。指导 agent 通过 WebAPI 帮员工完成：费用申请单提交 → 下推费用报销单 → 把发票挂进报销单「收票信息」（纯金蝶 WebAPI，员工无需登录金蝶） → 提交审批；以及出差申请单 → 下推差旅费报销单的同构流程。内置发票 OCR 清晰度/抬头校验、收票信息 vs 附件分流（行程单双算预警）、报销制度提醒、流程状态仪表板。制度与城市/招待费标准放 references/ 随 skill 分发。
---

# 金蝶费用/差旅报销全流程提交 Skill

本 skill 处理**写操作**（提交单据、下推、创建收票单、上传附件、提交审批），与只读导出的 `kingdee-data-exporter` 互补。两者共用 `config.py` 的 `KINGDEE_CONFIG`（base_url / acctid / username / password）。

## 四种单据与下推关系（formid 已实测/已确认）

| 单据 | formid | 下推目标 | 状态 |
|---|---|---|---|
| 费用申请单 | `ER_ExpenseRequest` | → 费用报销单 `ER_ExpReimbursement` | ✅ 已跑通 |
| 费用报销单 | `ER_ExpReimbursement` | （终态，提交审批后付款） | ✅ 已跑通 |
| 出差申请单 | `ER_ExpenseRequest_Travel` | → 差旅费报销单 `ER_ExpReimbursement_Travel` | ✅ 创建→提交→审核 已跑通 |
| 差旅费报销单 | `ER_ExpReimbursement_Travel` | （终态，提交审批后付款） | 🟡 实体结构与费用单**完全同构**；下推→挂票→改明细→写联动→传附件→体检**全通过**，仅 Submit 待实跑 |

> ⚠️ formid 拼写极敏感：`ER_ExpReimbursement`（正确，无多余 s）vs `ER_ExpenseReimbursement`（错误，曾因此误判"单据不存在"）。ExecuteBillQuery 查不到某单据时，先怀疑 formid 拼错或账号无该表单权限，不要直接认定"没部署"。

## 核心概念：报销单上"收票信息"与"附件"是两个位置（最易踩坑）

| 位置 | 单据体/接口 | 用途 | 参与金额校验？ |
|---|---|---|---|
| **收票信息** | 报销单 `RecInvInfo` 单据体 → 链接收票单 `IV_ReceivedInvoice` | **上传发票**（上传完自动进收票单） | ✅ 是，"发票金额≥报销金额"校验读这里 |
| **附件** | `AttachmentUpLoad`（一步挂单）/ `UploadFile`+`BOS_Attachment` Save | 行程单、补充说明等 | ❌ 否 |
| **联动表** | 报销单 `FReimbAndRecInvInfo` 单据体 | 「明细 ↔ 发票分录」对应关系（= UI「合并生成费用明细」的底层） | ❌ 否，但决定明细是否"与发票一致" |

- 校验报错"发票金额不允许小于报销金额" = 收票信息里**没有**有效的收票单（发票没传对位置，或被传到了附件）。
- `FEInvoiceEntity` 是报销单另一个发票子表，**不是**收票信息，历史单多为空，不要往这里填发票来绕过校验。

## 标准流程（费用申请单 → 报销单）

1. **提交费用申请单**：Save `ER_ExpenseRequest`（模板见 `references/field_cookbook.md`，含 F_TJKT_Base_qtr 项目必填等坑）。默认"仅保存暂存"，确认后再 Submit。
2. **下推报销单**：Push `ER_ExpenseRequest` → `ER_ExpReimbursement`（`TargetFormId` 必填，启用默认规则）。生成报销单（草稿 Z，BillNo 空）。
3. **发票先"找"再"建"**（顺序很重要）：
   - **优先查**：`helpers/recvin_link.py find <发票号码>` 在金蝶收票池 `IV_ReceivedInvoice` 里找收票单。
     本环境「发票云自动归集」开着，数电票开票次日凌晨自动生成收票单 → **多数情况直接能查到**。
   - **查不到才建**：用 WebAPI Save `IV_ReceivedInvoice` 建收票单（字段见 cookbook §3）。
4. **把发票挂进「收票信息」**（**已打通，纯金蝶 WebAPI**，不必用发票云/客户端）：
   ```bash
   python helpers/recvin_link.py link <报销单FID> <收票单号>            # 追加
   python helpers/recvin_link.py link <报销单FID> <收票单号> --replace  # 覆盖
   python helpers/recvin_link.py clear <报销单FID>                     # 清空
   python helpers/recvin_link.py precheck <报销单FID>                  # 提交前体检
   python helpers/recvin_link.py set-contact <报销单FID> <员工号>       # 补往来单位
   python helpers/recvin_link.py submit <报销单FID>                    # 提交（带体检）
   python helpers/recvin_link.py verify <报销单FID> <收票单号>          # 一键自检
   ```
   写法要点：`Model = {"FID":.., "FRecInvInfo":[{"FRecInv":{"FBillNo":"<收票单号>"}}]}`
   —— 字段**必须放在 `FRecInvInfo[]` 数组行内**，放单据头会报 `实体不存在此属性！[EntityType：BillHead]`。
   详见 cookbook §4.2。
   - ⚠️ **防抢关联**：一张收票单只能属于一张报销单，挂到第二张会静默抢走原单的关联。
     `recvin_link.py` 已内置拦截（`--allow-steal` 才放行）。
   - ⚠️ 已审核单据同样可写，操作前先看单据状态，别误改历史单。
5. **让明细与发票一致（"合并生成费用明细"）**：明细行金额应 = 实际报销额，且**必须 ≤ 发票价税合计**。
   - 金蝶 18 个操作里**没有**「合并生成费用明细」这个可调 API —— 它只是 UI 选票时的联动动作，
     底层落到两处数据：① 明细行 `FReimbLinkInvCode`/`FRecInvBillNo`/`FInvNumber`/`FInvOpenDate` 打标；
     ② 联动表 `FReimbAndRecInvInfo` 逐条发票分录（`FRecInvFid`/`FRecInvEntryId`/`FInvAllAmt`）。
   - ```bash
     python helpers/expense_edit.py set-detail <FID> --entry <明细分录内码> --amount 8.00
     python helpers/expense_edit.py linkage <FID> --recv SPD00008634 --entry <明细分录内码>
     ```
   - `recvin_link.py set-contact` 要先跑：`ValidateFlag=true` 时 `FCONTACTUNIT` 是**必填**（MsgCode=11）。
6. **附件分流**：行程单/说明走 `AttachmentUpLoad`，**不要**传收票信息（会双算）。
   ```bash
   python helpers/expense_edit.py attach <FID> "D:/x/行程单.pdf"
   ```
   ⚠️ **`IsLast` 必须显式传 `true`** —— 漏传会被反序列化成 `false`，接口照样返回 `IsSuccess=true` 和一个
   `FileId`，但**内容根本没进文件信息表**（回环下载报「数据库文件信息表中不存在编码为 X 的文件信息」）。
   helper 已内置「上传后立刻回环下载比 md5」的校验，别只看 IsSuccess。
7. **提交审批**：`precheck <FID>` 体检 → `submit <FID>`（有阻断项会自动中止）。
   已实测走通：A(暂存) → **B(已提交)**，**提交后收票信息保持**。
8. （可选）付款阶段由财务在金蝶操作，本 skill 只负责到"提交审批"。
9. **收尾自检**：`python helpers/recvin_link.py verify <报销单FID> <收票单号>`
   —— 会同时校验报销单侧行数和收票单侧 `LINKBILLID/LINKIVNUMBER` 回写，全绿才算成功。
   再加一条 `python helpers/expense_edit.py check <报销单FID>` 看金额链条（报销 ≤ 发票、联动合计 = 发票合计）。

## 金额与费用项目口径（提交前必须与用户确认）

- **硬规则**：`报销金额 ≤ 发票价税合计`，否则金蝶报「发票金额不允许小于报销金额」。
- **正常情况**：按**实际发生额**报销，明细金额跟发票走。申请单金额只是预估，
  实际发生可在报销时调整（例：申请 100 元招待 80 + 交通 20 → 实际发票 90 元招待 85 + 交通 5，
  **明细就按发票写 85 / 5**，必要时连**费用项目**一起改）。
- **发票 > 申请单**：先与用户确认按哪个金额提交，并**排查是否"多开"**。
  若属多开，**不能按发票金额**，必须按**实际付款金额**报销（发票仅作佐证）。
- **发票 < 申请单**：属正常下调，按发票金额提交（本次实测：申请 10.00 → 发票 8.00 → 报销 8.00）。
- **⚠️ 凡申请单 ↔ 发票不一致，一律先跟用户确认再提交**，不要自行拍板。
- **网约车**：必须同步提交**行程单**。识别到行程单就传**附件**；未识别到要**提醒用户财务可能驳回**
  （但不硬拦截，建议同步提交）。
  **行程单金额绝不能当作发票金额** —— 本环境系统会把行程单也识别成发票金额造成双算。

## 跨组织报销（2026-09-15 实测，务必看）

**问题**：能不能把 A 组织的发票挂到 B 组织的报销单上？（例：购方「示例科技」的票，给「示例四科技」/「示例二科技」报销）

**结论：能，WebAPI 完全接受**。实测：
- 源单 101570（org 104 示例四科技）→ 下推 101720 → 挂示例科技的收票单 `SPD00008634` ✅ 成功
- 源单 101571（org 105 示例二科技）→ 下推 101721 → 挂示例科技的收票单 `SPD00008518` ✅ 成功
- 收票单侧 `FLINKBILLID/FLINKIVNUMBER` 照常回写

### ⚠️ 副作用：金蝶会**改写收票单的结算组织**
收票单上有两个组织字段：`SOURCEORGID`（来源/购方组织）与 `SETTLEORGID`（结算组织）。
挂票后 `SETTLEORGID` 会被**改成目标报销单的组织**：

| 收票单 | SOURCEORGID | 挂票前 SETTLEORGID | 挂到 org 104 后 |
|---|---|---|---|
| SPD00008634 | 101 示例科技 | 101 | **104 示例四科技** |
| SPD00008518 | 101 示例科技 | 101 | **105 示例二科技** |

→ 跨组织挂票**不是只读引用**，它改动了来源组织的收票单，可能影响其**进项税归属 / 收票核算**。
提交前必须让用户知情。

### ⚠️「没有购买发票模块」**不等于**收票信息不可用（实测推翻）
示例二科技（org 105）据称未购买发票模块，但：
- 收票信息写入 **成功**（1 行，无任何报错）
- 收票单在 `CreateOrgId` = 0 / 101 / 104 / 105 下**均可见**，没有可见性拦截

**正确理解**：发票模块影响的是**发票自动归集**（发票云把票自动收进收票池 → 自动生成收票单），
**不是**报销单「收票信息」这个子表本身。所以：
- 没有发票模块的组织 → 它的发票**不会自动进收票池**，需要人工建收票单或用附件
- 但只要收票单已存在（哪怕是别的组织建的），就能挂上收票信息

### 🔴 强制确认节点：`发票购方名称 ≠ 报销单组织名称`
这是**税务归属**问题（谁的进项、谁抵扣），不是技术问题。实测两张票购方都是
「示例科技有限公司」，而报销组织是「示例四科技有限公司」/「示例二科技有限公司」。
→ **一律先与用户确认再提交，不要自行拍板**（与「申请单↔发票金额不一致」同级）。

## 发票处理（收票单创建 + 附件分流 + 行程单预警）

1. **收文件先做分类**（调用 `helpers/invoice_classifier.py`）：
   - **发票**：有发票号码、购销方税号、价税合计、税率等要素。
   - **行程单**：机票行程单（航班号、出发/到达机场、旅客姓名、票价/税费/总金额）；火车票（车次、出发/到达站、席别）；住宿清单等。
2. **发票** → **先查收票池**（`IV_ReceivedInvoice`，用 `recvin_link.py find <发票号>`），查到就直接挂收票信息；
   查不到才用 Save 建收票单 `IV_ReceivedInvoice`（字段见 cookbook §3）。
3. **行程单** → 传**附件**（`UploadFile`+`BOS_Attachment`），并提醒用户"行程单请传附件，避免与发票重复计入导致汇总金额虚高被财务驳回"。
4. **前置校验**（提交系统前先做，不合规打回）：
   - 清晰度：模糊/缺角/反光/非发票 → 打回重拍。
   - 购方抬头：OCR 提取购方名称，须 = 申请组织名称（如示例科技有限公司）；不一致拦截并说明。
   - 金额匹配：发票价税合计应与报销金额匹配（允许合理差异）。

### ⚠️ 行程单误传收票信息 → 金额双算预警（必须实现）

若用户坚持把**行程单**传到收票信息（而非附件）：系统 OCR 也会识别金额，导致同一笔业务被"发票+行程单"重复计入，汇总金额虚高。

处理：
- **识别到行程单却要传收票信息** → 先提示风险，计算并展示**当前汇总金额**给用户，明确警告"可能被财务驳回"；用户仍坚持则照传（记录日志）。
- 优先引导：行程单走附件，发票走收票信息。

## 报销制度提醒（references/policy_rules.md，随 skill 分发）

- 差旅费：按城市分级的住宿/餐饮/交通限额，超标时主动提醒"XX 城市住宿上限 N 元/晚，本单 M 元超标，是否拆单或特批？"
- 业务招待费：需提供 **CEO 同意的微信截图**作为附件；缺失则提醒"业务招待费需上传 CEO 微信同意截图，否则财务会驳回"。
- 制度文件随 skill 分发（不放对话背景），外部用户安装后即带，agent 在对话中按规则主动提醒。

## 流程状态仪表板（规划）

- 定时查询各单据 `FDocumentStatus`（A暂存/B已提交/C已审核/D 等）+ 下推/付款关联，落本地 SQLite。
- 按人/按单/按状态分组，生成 HTML 仪表板，每日推企业微信（复用现有推送机制）。
- 状态口径：待提交 / 审核中 / 已通过 / 已报销 / 待打款。

## 已知环境坑（务必先看 references/field_cookbook.md）

- **`Save` 字段必须带 `F` 前缀**：`View` 返回的 `SUMALLAMOUNT`/`ExpenseAmount` 等，Save 时要写成 `FSUMALLAMOUNT`/`FExpenseAmount`，否则被静默忽略。
- **报销单 `FCONTACTUNIT`(往来单位)**：下推后为空，Submit 前必须局部更新。Save 键为 `FCONTACTUNIT`/`FCONTACTUNITTYPE`，引用用 `{"FNumber":"员工号"}` 或 `{"Id":员工Id}`。
- **✅ 收票信息 `RecInvInfo` 可以用纯金蝶 WebAPI 写入（2026-09-14 打通，此前"写不进"的结论是错的）**：
  `Save ER_ExpReimbursement`，`Model={"FID":<FID>,"FRecInvInfo":[{"FRecInv":{"FBillNo":"<收票单号>"}}]}`。
  实测与官方金税互联插件写入结果逐字段一致，收票单侧 `LINKBILLID/LINKIVNUMBER` 自动回写。
- **`FRecInv`(收票单) 元数据标着 `IsNewLock=IsEditLock=True`，但它照样能被 WebAPI Save 写入**。
  ⚠️ **不要只凭 `IsNewLock/IsEditLock` 就断定某字段"写不进"** —— 这是本次绕了远路的根源，必须实测。
- **字段层级是历史 7 种写法全失败的真凶**：`FRecInv`/`FIVSerialNo` 必须放在 `Model.FRecInvInfo[]`
  **数组行内**。放到单据头层级会明确报错 `…实体不存在此属性！[EntityType：BillHead Propeyt…]`
  —— 看到 `[EntityType：BillHead]` 就是层级放错了。
- **`FIVSerialNo`(发票序列号) 不必写**：官方流程产物的 `FIVSERIALNO` 实测是空的（101714/101716 均为 `" "`），
  承载关联的是 `FRecInv`。它是可选装饰，不是入口。
  （顺带：它的正确值若真要填，是收票单 `FPDFURL` 尾部 **32 位** hash，**不是** `FPIAOZONESERIALNUMBER` 那个 33 位。）
- **防抢关联**：一张收票单只能属于一张报销单。把它挂到第二张单上会**静默改掉**原单的关联，
  造成"原单还列着这张票、票却说属于别人"的不一致。写之前先看收票单的 `FLINKBILLID`；
  `recvin_link.py` 默认拦截，`--allow-steal` 才放行。
- **已审核(C)单据的收票信息同样可 Save 改**（不报错）→ 操作前务必先判单据状态。
- **收票单必须先存在**：本环境发票云自动归集是开着的，数电票开票次日凌晨自动生成收票单
  （`FGENERATETYPE`=3/4）。「发票号码查不到收票单」多为：纸票未采集 / 号码错 / 属其它组织。
- **`ExecuteBillQuery` 字段名与 View 不同且写错就报错**：View 里叫 `PDFURL`/`PURNAME`，查询要写
  `FPDFURL`/`FPURNAME`；另外**返回列顺序 = `FieldKeys` 顺序**（实测），可安全按位置解析。
- **判定"某字段能不能写"**：`QueryBusinessInfo`（见 cookbook §4.2）能拿到 `Entrys[]/Operations[]` 结构，
  用来**看结构、拿实体名**很有用；但可写性**不能只看 IsNewLock/IsEditLock**，要实测。
  注意 **View 用 `EntryName`（`RecInvInfo`），Save 用 `Key`（`FRecInvInfo`）**。
- **标准库直连金蝶必须带 CookieJar**：登录后靠会话 Cookie 认身份，裸 `urllib.urlopen` 会拿到
  「会话信息已丢失，请重新登录」，且 `View` 返回空字典**不报错**（极易误判成"单据没数据"）。
- **编辑锁冲突（"…业务操作-\"[费用报销单-FYBX…-修改]\"冲突，请稍候再使用。"）是瞬时的，重试即可**：
  实测同一脚本内重试 1 次即成功（`recvin_link.py` 已内置自动重试）。
  ⚠️ 旧笔记写的"需客户端关闭或重下推新单"**是过度结论，别照做**。
- **收票单 `IV_ReceivedInvoice` 的 Save Model 是「扁平结构」**：表头字段（`FIVNUMBER`/`FOPENDATE`/
  `FSUMAMOUNT`/`FSUMTAXAMOUNT`/`FSUMALLAMOUNT`/`FPURNAME`/`FSALENAME`…）**直接写在 Model 根**，
  **不要**用 `FBillHead` 包裹 —— 包了会 `IsSuccess=true` 但表头字段**全部静默丢弃**（只剩分录行）。
  官方《收票单》文档的 Model 示例就是扁平写法。
- **更新收票单分录要 `IsDeleteEntry=true`**：不带 `FEntryID` 直接再 Save 一次会**追加**分录（2 行变 4 行），
  必须用 `IsDeleteEntry="true"` 清旧重建；该开关只清分录，**不动表头**。
- **`NeedUpDateFields` 与「新增分录行」互斥**：指定 `NeedUpDateFields` 后，Model 里**其它实体的新行
  （如联动表 `FReimbAndRecInvInfo`）会被静默丢弃**。改已有字段用一次 Save（带 NeedUpDateFields），
  新增分录行**必须另起一次 Save**（不带）。
- **`ValidateFlag="true"` 时 `FCONTACTUNIT`(往来单位) 是必填**，不填 MsgCode=11 报
  「字段"往来单位"是必填项」。`recvin_link.py` 的 `save()` 默认 `ValidateFlag=false` 所以不报 ——
  但**别因此以为往来单位可选**。规律：`FCONTACTUNIT` = **申请人本人**（= `ProposerID`，官方单据实测一致），
  类型 `BD_Empinfo`，引用用 `{"FNumber":"员工号"}` 或 `{"Id":员工内码}`。
  （Submit 本身不校验它，所以"空着也能提交成功"，但单据不完整。）
- **附件上传两个可用端点 + `IsLast` 必填**：
  - `DynamicFormService.UploadFile.common.kdsvc`：只上传拿 `FileId`。必填 `FileName` + `SendByte`(base64) + **`IsLast:true`**。
  - `DynamicFormService.AttachmentUpLoad.common.kdsvc`：一步上传并挂到单据。必填
    `FileName`/`FormId`/`IsLast`/`InterId`(单据内码)/`BillNO`(单据编号)/`SendByte`。
  - `DynamicFormService.AttachmentDownLoad.common.kdsvc`：回环下载，传 `FileID`，返回 `FilePart`(base64)+`FileSize`+`FileName`。
  - ⚠️ `FileUploadService`、`GetAttachmentList`、`DownloadFile`、`GetFileInfo` **都不存在**；
    `Attachment.AttachmentService` 命名空间也不存在。别浪费时间猜。
  - ⚠️ `BOS_Attachment` 用 `ExecuteBillQuery` 查会报「数据库执行异常」（不是普通可查业务对象），
    **验证附件一律走 `AttachmentDownLoad` 回环比字节**。
  - 报错信息会直接说「data数据包中的参数X必传」→ 想知道契约就发空包读报错，比翻文档快。
- **「合并生成费用明细」没有对应的可调操作**（18 个 Operation 里没有）。它的效果 = 写
  联动表 `FReimbAndRecInvInfo` + 明细侧 `FReimbLinkInvCode`。明细侧那几个字段元数据标着
  `IsNewLock/IsEditLock=True`，但**实测可写**（又一次印证"锁标记 ≠ 写不进"）。
  对照真实样本 `FYBX20260101000004`(FID 101698) 复刻结构最稳。
- **联动表不是必需的**：真实已审核单 101714/101716 的 `FReimbAndRecInvInfo` 就是 0 行，照样审核通过。
  只有走过 UI「联动处理」的单才有（如 101698 有 5 行）。所以别把"联动 0 行"当成提交阻断项。

- **`ExecuteBillQuery` 报错时返回「双层 list」包裹体**（`[[{"Result":{"ResponseStatus":…}}]]`）：
  旧版 `_unwrap_error` 只判 `r[0]` 是不是 dict，遇到嵌套 list 会**静默跳过**，
  于是 `query()` 把**错误对象当成一行业务数据返回**（不报错、不抛异常）。
  典型触发：字段名写错——**给报销单查 `FCostOrgID` 会报「元数据中标识为FCostOrgID的字段不存在」**，
  报销单的组织字段叫 `FOrgID` / `FExpenseOrgId` / `FPayOrgId`（`FCostOrgID` 是**费用申请单**才有）。
  已修（2026-09-15）：先剥掉所有嵌套 list 再判。
- **出口代理会偶发瞬时故障，不是金蝶的问题**（2026-09-15 实测）：
  形态为 `URLError: Tunnel connection failed: 502 Bad Gateway` 或
  `URLError: _ssl.c:1015: The handshake operation timed out`，
  实战中**连错 4 次第 5 次成功**。旧版 `_post` 没有重试，会把网络抖动误报成业务失败。
  已修：`_post` 默认**重试 5 次 / 间隔 6s**。

## 复用脚本与代码

- **`helpers/recvin_link.py`（本 skill，★核心）**：报销单「收票信息」写入 —— 纯金蝶标准 WebAPI（仅标准库，
  零第三方依赖）。子命令 `find` / `list` / `link` / `clear` / `precheck` / `set-contact` / `submit` / `verify`，
  支持费用报销单与差旅费报销单，内置防抢关联保护 + 编辑锁自动重试。
  凭证走环境变量（`KINGDEE_BASE_URL/ACCTID/USERNAME/PASSWORD`）或 `config.py`，便于分发给其他公司。
- **`helpers/expense_edit.py`（本 skill，★配套）**：报销单「明细 ↔ 发票对齐 + 附件」。子命令
  `attach`（附件上传，含 `IsLast` 坑与回环校验）/ `set-detail`（改明细金额/费用项目/税率）/
  `linkage`（写明细↔发票联动 = 合并生成费用明细）/ `download`（回环下载校验）/ `check`（金额链条体检）。
- `helpers/piazzone_tool.py`（本 skill，可选兜底）：发票云 API，仅在"发票不在收票池、需程序化采集"时才有意义；
  按官方文档它只是**告知发票云**绑定关系，不回写金蝶。已内置域名 failover。
- `helpers/invoice_classifier.py`（本 skill）：发票 vs 行程单分类、字段提取、双算预警。
- `references/field_cookbook.md`：费用申请单 / 出差申请单的 Save 模板（含全部必填字段）。
下推：`Push` 接口，`formid` 填源单表单、`TargetFormId` 填目标表单（如 `ER_ExpReimbursement`）。
新建收票单：标准 `Save IV_ReceivedInvoice`，Model 用**扁平结构**（见 cookbook §3.3）。
- `官方接口说明/`：金蝶 WebAPI 官方原始文档（Save / Submit / 收票单）。
