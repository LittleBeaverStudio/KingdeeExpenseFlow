# 金蝶费用/差旅流程 — 字段与接口配方手册（实测）

> 所有接口 base：`{base_url}/k3cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.{方法}.common.kdsvc`  
> 登录：`AuthService.ValidateUser.common.kdsvc`，body `{"acctid","username","password","lcid":2052}`，校验 `LoginResultType==1`。  
> 通用查询：`ExecuteBillQuery`，body `{"formid","data": json.dumps({"FormId","FieldKeys"(逗号分隔字符串!),"FilterString","OrderString","TopRowCount","StartRow","Limit"})}`

---

## 0. 头等大事：Save 字段必须带 `F` 前缀

`View` / `ExecuteBillQuery` 返回的字段名**经常不带 `F` 前缀**（如 `BillNo`、`SUMALLAMOUNT`、`ExpenseAmount`、`RecInvInfo`），但 **`Save` 的 Model 里字段名必须带 `F` 前缀**（如 `FBillNo`、`FSUMALLAMOUNT`、`FExpenseAmount`）。

- 写 `Save` 时，把 `View` 返回的字段名统一加上 `F`；单据体实体名同理（`Entity` → `FEntity`，`RecInvInfo` → `FRecInvInfo`）。
- 部分字段例外：`_Id` 结尾的外键标量（如 `RecInv_Id`）、系统字段 `Id`、`Seq` 等，通常保留原样。
- **坑**：用 View 返回的非 `F` 前缀字段去 `Save`，金蝶会**静默忽略**该字段，不会报错，导致金额/链接不生效。

---

## 1. 费用申请单 `ER_ExpenseRequest`（Save 提交）

表头关键字段：
- `FBillTypeID` = `FYSQ001_SYS`
- `FOrgID` / `FCostOrgID` / `FPayOrgID` = `{"FNumber":"101"}`
- `FStaffID` = `{"FSTAFFNUMBER":"123"}`（员工号）
- `FDeptID` = `{"FNumber":"BM03"}`
- `FCurrencyID` / `FLocCurrencyID` = `{"FNumber":"PRE001"}`
- `FExchangeTypeID` = `{"FNumber":"HLTX01_SYS"}`（固定汇率）
- `FTOCONTACTUNITTYPE` = `BD_Empinfo`
- `FDate` = `"2026-08-26"`
- `FReason` = 事由（必填）

单据体 `FEntity`（每行）：
- `FExpenseItemID` = `{"FNumber":"044"}`（业务招待费；用历史单反查，本环境 BD_ExpenseItem 业务对象查不到）
- `FOrgAmount` / `FLocAmount` = 金额
- **`F_TJKT_Base_qtr` 必填**（自定义"项目"基础资料，如 `{"FNumber":"ZT00"}` 植体公用）——漏填会被校验拦截
- `FEntryCostDeptID` = `{"FNumber":"BM03"}`

> 本环境 `BD_ExpenseItem` / `BD_STAFF` 业务对象标识查不到，员工/费用项目用历史单据反查。员工速查：张三=123/BM03(财务部)，钱八=140/BM08(销售)，周九=009/BM01或BM04。

---

## 2. 下推 `Push`（费用申请单 → 费用报销单）

body：
```json
{"formid":"ER_ExpenseRequest",
 "data": json.dumps({
   "Ids":"","Numbers":["源单号"],
   "RuleId":"","TargetFormId":"ER_ExpReimbursement",
   "IsEnableDefaultRule":"true","IsDraftWhenSaveFail":"true","CustomParams":{}
 }, ensure_ascii=False)}
```
- `TargetFormId` **必填**（启用默认转换规则时）。
- 返回 `SuccessEntitys[0].Id` = 新报销单 FID（草稿 Z，BillNo 空）。
- 若源单已下推，可能再生成一张新报销单（本环境允许），注意去重。

---

## 3. 收票单 `IV_ReceivedInvoice`（发票上传进收票信息 = 建这张单）

收票单是 报销单「收票信息」校验的数据来源。标准 WebAPI 可以 `Save` 创建/修改收票单，**但无法直接把收票单写进报销单的 `RecInvInfo`**（见 4.2 限制）。

### 3.1 Save body Model 关键字段（均已实测可写）

| 字段 | 示例值 | 说明 |
|---|---|---|
| `FIVNUMBER` | `"24000000000000000001"` | 发票号码 |
| `FIVCODE` | `""` | 发票代码（电子发票常空） |
| `FINVOICETYPE` | `"26"` | 电子发票 |
| `FOPENDATE` | `"2026-08-26"` | 开票日期 |
| `FSUMAMOUNT` | `10.0` | 不含税金额 |
| `FSUMTAXAMOUNT` | `0.30` | 税额 |
| `FSUMALLAMOUNT` | `10.30` | **价税合计**（校验读这个） |
| `FPURNAME` | `"示例科技有限公司"` | 购方名称 |
| `FPURTAXNUMBER` | `"91110000000000000X"` | 购方税号 |
| `FSALENAME` | `"示例出行科技有限公司"` | 销售方名称 |
| `FSALETAXNUMBER` | `"91110000000000001X"` | 销售方税号 |
| `FISEXAMINE` | `"1"` | 已认证 |
| `FGENERATETYPE` | `"3"` | 生成方式 |
| `FSpecialBusinessType` | `"12"` | 特殊业务类型 |
| `FSTATUS` | `"0"` | 状态 |
| `FSOURCEORGID` | `{"FNumber":"101"}` | 来源组织 |
| `FSETTLEORGID` | `{"FNumber":"101"}` | 结算组织 |
| `FLINKBILLTYPE` | `"ER_ExpReimbursement"` | 链接单据类型 |
| `FLINKBILLID` | `100008` | 链接报销单 FID |
| `FLINKIVNUMBER` | `""` | 链接报销单编号（草稿为空） |

> 图片 `PICTUREURL` 走票总管(piaozone.com)外部服务；本环境发票图 OCR 由发票云/票总管完成，WebAPI 直建收票单可不带图（金额字段即可）。

### 3.2 发票明细行 `FEntity`（必须带，否则报销单校验读不到金额）

报销单校验本质是按 `RecInvInfo` 汇总收票单金额；收票单 header 金额即使正确，若没有明细行，系统生成的 `FReimbAndRecInvInfo` 为空，校验仍会失败。

View 返回键（无 F 前缀）：
```json
{
  "Id": 145481, "Seq": 1,
  "ITEMNAME": "*交通运输服务*客运服务费",
  "UNIT": "次", "SPECIFICATIONS": "无",
  "QTY": 1.0, "PRICE": 10.0, "AMOUNT": 10.0,
  "TAXRATE": 3.0, "TAXAMOUNT": 0.3,
  "TAXCODE": "3010101020203000000",
  "TOTALAMOUNT": 10.3
}
```

Save 时必须加 `F` 前缀：
```json
{
  "FITEMNAME": "*交通运输服务*客运服务费",
  "FUNIT": "次", "FSPECIFICATIONS": "无",
  "FQTY": 1.0, "FPRICE": 10.0, "FAMOUNT": 10.0,
  "FTAXRATE": 3.0, "FTAXAMOUNT": 0.3,
  "FTAXCODE": "3010101020203000000",
  "FTOTALAMOUNT": 10.30
}
```

### 3.3 ⚠️ Save Model 必须是「扁平结构」（2026-09-14 实测踩坑）

**表头字段直接写在 `Model` 根上**，官方《收票单》文档示例即扁平写法：

```json
{"NeedUpDateFields":[],"IsDeleteEntry":"false","ValidateFlag":"false","IsAutoAdjustField":"true",
 "Model":{"FID":118635,
   "FIVNUMBER":"24000000000000000001","FOPENDATE":"2026-09-14","FDRAWER":"孙七",
   "FSUMAMOUNT":7.77,"FSUMTAXAMOUNT":0.23,"FSUMALLAMOUNT":8.00,
   "FPURNAME":"示例科技有限公司","FPURTAXNUMBER":"91110000000000000X",
   "FSALENAME":"示例出行科技有限公司","FSALETAXNUMBER":"91110000000000001X",
   "FINVOICETYPE":"26","FSTATUS":"0","FISELECTRONIC":"true",
   "FEntity":[{...},{...}]}}
```

- ❌ **不要**写成 `{"FID":118635,"FBillHead":{...表头字段...},"FEntity":[...]}` ——
  这样 Save 会返回 `IsSuccess=true`、`MsgCode=0`，但**表头字段全部静默丢弃**（只剩分录行），
  表现为 "金额/日期/购销方全是 0 或空，明明没报错"。**这是最容易被骗过的一种失败。**
- 判据：写完必须 View 回读逐字段核对，别只看 IsSuccess。
- **更新分录用 `IsDeleteEntry="true"`**：不带 `FEntryID` 再 Save 一次会**追加**分录（2 行 → 4 行）；
  该开关只清分录、不动表头。
- 收票单是**手工建**时 `FGENERATETYPE` 为 `" "`（空），自动归集的是 `3`/`4`。
  ⚠️ **~~二者在报销单侧无差别~~ —— 这句是错的，2026-09-22 实测推翻**：
  手工建的票**拿不到发票云流水号**（`FPIAOZONESERIALNUMBER`/`FPDFURL`/`PICTUREURL` 全空，
  `ISEXAMINE=0`），而自动归集的票**全部有**（全池 1000 张有流水号 1000 张）。
  即使发票号、购销方名称+税号、金额、税额、开票日、明细行**全部正确且组织一致**，也照样拿不到。
  → 原因：流水号是**发票云采集时回写**的，手工建单只是往金蝶插一行，发票云那边一无所知。
  → ✅ **挂到「同组织」的报销单上，业务校验是通的**（2026-09-22 实测，见 SKILL.md）：
    `strict_probe`（`ValidateFlag=true`）**没有任何"发票云流水号"相关报错**；唯一与发票相关的校验是
    「发票金额不允许小于报销金额」，把明细金额对齐到 ≤ 发票金额后即消失。**流水号不参与校验。**
  → 🔴 **别指望"先建单、后补号"**（本结论已被**两路独立证据**钉死，2026-09-22）：
    ① 把该票的 PDF 传到收票单**附件**（`AttachmentUpLoad` 成功、回环字节一致）后，立即与 25 秒后
    各回读一次，`FPIAOZONESERIALNUMBER`/`FGENERATETYPE`/`FISEXAMINE`/`FPDFURL` **全部无变化**
    → **附件 ≠ 采集**，金蝶不会因此去发票云找同号票回写流水号。
    ② **用户本人实测**：手工建的票，**事后从电子税务局把同一张票再下载一次，仍然没有流水号**
    → 连"事后补采集"也补不上。**"手工建票兜底"这条路不成立。**
  → ✅ **因此既定路线只有一条**：**让员工在报销软件里把这张票上传一次**（当天进池、当天有流水号），
    再由 agent 挂票提交。唯一能稳定产出流水号的就是**采集那一刻**（`GEN='3'` 发票云采集 /
    `'4'` 归集账号批量导入）。
    ⚠️ **"定时每天下载昨日发票"不可行**（2026-09-22 用户确认）：金蝶里点「下载」每次都要
    **电子税务局账号的手机验证码**，无法无人值守。
  → 🔴 **手工建票不校验发票号重复**：拿一张发票云里已有的票号新建，`Save` 照样成功，
    池里会出现两条同号记录 → **建前必查重、测后必删**（员工账号无删除权限，用有权限账号）。
- 🔴 **`Submit` 之后就撤不回来了**（2026-09-22 实测）：`Delete` 只允许 `Z`/`A`/`D` 状态
  （状态 `B` 报「只能删除创建，暂存，重新审核状态的数据!」）；`UnAudit` 报「已关联工作流实例，
  不支持通过 WebAPI 接口进行传统审批」；`UnSubmit` 是**不存在的服务名**（返回非 JSON）。
  → 用测试单验证「无发票能否提交」时要清楚：**提交后只能人工在 UI 驳回/撤销**。
- ⚠️ **查收票池受「账号可见性」限制**：普通员工账号**只看得到 `FCreatorId` = 自己**的收票单
  （实测钱八 24 张 vs 取数账号 1000 张）。所以 `find` 查不到**不代表池里没有**——先换有权限的账号。
- 本环境发票号码在收票池里**基本无重复**（扫 1000 张、878 个号码无重复）→ 手工建票前先
  `recvin_link.py find <发票号>`，查到就别再建，避免撞车。

### 3.4 保存方式建议

对已有收票单做修正时，建议**全量 Save**（`NeedUpDateFields: []`），把所有 header 字段 + 一条 `FEntity` 行完整传入，避免局部更新遗漏。

---

## 4. 报销单 `ER_ExpReimbursement` — 收票信息链接 + 往来单位 + 提交

### 4.1 报销单结构（View 全字段确认）
- 表头：`OrgID`/`CurrencyID`/`ExchangeTypeID`、`FEInvoiceEntity`（发票子表，空，非收票信息）、**`RecInvInfo`**（收票信息，链接收票单）、`FReimbAndRecInvInfo`（报销与收票关联，系统自动生成）、`CONTACTUNITTYPE`/`CONTACTUNIT`（往来单位）、`Causa`(事由)
- 明细 `ER_ExpenseReimbEntry`：从源单带出 `ExpenseAmount`/`ExpID`(费用项目)/`InvoiceType`/`TaxRate`/`TaxAmt`/`SourceBillType`/`SourceBillNo`/`F_TJKT_Base_83g`(项目)/`FInvNumber`/`FInvOpenDate`
- `FSrcEntry`：源单关联

### 4.2 ✅ 收票信息链接 —— **已打通**（2026-09-14 实测，本节为准）

> **一句话结论**：报销单「收票信息」可以用**纯金蝶标准 WebAPI `Save`** 写入，写入结果与官方
> 「金税互联 / 发票助手」插件写入的**逐字段一致**。不需要发票云 API、不需要 websocket、
> 不需要员工登录金蝶。历史"写不进"的判断是**错的**，根因见 §4.2.2。

**可用工具**：`helpers/recvin_link.py`（本 skill 自带，纯标准库）
```bash
python helpers/recvin_link.py find 24000000000000000001      # 按发票号找收票单
python helpers/recvin_link.py list 100001                    # 看报销单现有收票信息
python helpers/recvin_link.py link 100001 SPD00000001        # 挂上去（追加）
python helpers/recvin_link.py link 100001 SPD00000001 --replace   # 覆盖式写入
python helpers/recvin_link.py clear 100001                        # 清空全部
python helpers/recvin_link.py verify 100001 SPD00000001      # 定位→写→回读→联动校验
python helpers/recvin_link.py --travel link 100001 SPD00000001     # 差旅费报销单
```

**核心写法（必须严格遵守层级）**：
```python
POST {base}/k3cloud/.../DynamicFormService.Save.common.kdsvc
formid = "ER_ExpReimbursement"            # 或 "ER_ExpReimbursement_Travel"
data = {
  "IsDeleteEntry": "false",               # false=追加（安全） / true=覆盖（删除未列出的行）
  "Model": {
    "FID": 100001,
    "FRecInvInfo": [                      # ★ 实体数组，实体名用 Save Key = FRecInvInfo
      {"FRecInv": {"FBillNo": "SPD00000001"}}      # ★ 收票单引用，FBillNo = 收票单号
    ]
  }
}
```
- **只需收票单号即可**，`FIVSerialNo` 不必写（官方流程也留空，见 §4.2.3）。
- `IsDeleteEntry=false` → 追加（已挂过的用代码里的 skip_existing 去重）。
- `IsDeleteEntry=true` + 给出要保留的行 → 覆盖式写入，未列出的行被删除。
- ⚠️ **清空写法很反直觉（实测）**：`IsDeleteEntry=true` + `FRecInvInfo: []`（空数组）**无效**，行还在
  （加 `NeedUpDateFields=["FRecInvInfo"]` 也无效）；但 `FRecInvInfo: [{}]`（**一个空行对象**）→ ✅ **清空到 0 行**。
  工具里是 `clear` 子命令 / `Kingdee.clear_linked()`。
- ⚠️ `IsDeleteEntry=true` **不会**误删其它实体：实测写 `FRecInvInfo` 后
  费用明细 `FEntity`(=View 的 `ER_ExpenseReimbEntry`) / `FMultiPayeeEntity` / `FSrcEntry` 行数不变。

**实测验证（示例科技生产环境，2026-09-14）**：
| 验证项 | 结果 |
|---|---|
| 报销单 100001 挂收票单 SPD00000001 | ✅ `RecInvInfo` 1 行，发票号/价税合计/销方全部自动带出 |
| 收票单侧自动回写 | ✅ 110001: `LINKBILLTYPE=ER_ExpReimbursement` `LINKBILLID=100001` `LINKIVNUMBER=FYBX20260101000001` `LINKBILLDATE=2026-09-14` |
| 与官方单据对比（100004 ← 110002/110003） | ✅ 逐字段一致，含 `FReimbAndRecInvInfo`/`FEInvoiceEntity` 均为 0 行（我们不缺东西） |
| 差旅费报销单 | ✅ 实体结构完全相同，同一写法通用（未在真实差旅单上跑，结构已核对） |

### 4.2.1 ⚠️ 三个前置事实（决定你能不能用这条路）

1. **收票单必须先在金蝶收票池里，且必须有发票云流水号**（v2.0.11 起两条都是**硬闸门**）。
   查不到 = ① **这张票从没被采集过**（不是"系统会自动补"：每日"下载"要电子税务局手机验证码，
   无法自动化）→ 正确动作是**让员工在报销软件里上传一次**；② 号码错；③ 属其它组织。
   ❌ **不要手工建票**（`FGENERATETYPE` 为空 = 永远没流水号 = 提交必被拦）。
2. **一张收票单只能属于一张报销单**。把它挂到第二张单上会**静默抢走**原单的关联，
   原单出现"我这还列着这张票、票却说它属于别人"的不一致。`recvin_link.py` 默认**拦截**
   这种抢关联（`--allow-steal` 才放行），错误信息会直接告诉你是谁占着。
3. **写入不受单据状态限制**：实测**已审核(C)单据**的收票信息同样能 Save 改（不报错）。
   所以务必先判断单据状态，别误改历史已审单。

### 4.2.2 🔑 历史上三次失败的真实根因（都不是"字段被锁"）

| # | 曾经的判断 | 实测真相 |
|---|---|---|
| 1 | `FRecInv` 被字段级锁定（`IsNewLock`/`IsEditLock`=True），标准 Save 必然写不进 | ❌ **错**。实测 `FRecInv` **可以**被 WebAPI Save 写入。**不要只凭 IsNewLock/IsEditLock 就下"写不进"的结论** |
| 2 | 要把 `FIVSerialNo`（发票序列号）当入口写 | ❌ **错**。官方流程产物的 `FIVSERIALNO` 是空的（100003/100004 实测均为 `" "`）。承载关联的是 `FRecInv` |
| 3 | 位置是三级嵌套 `FEntity[].FEInvoiceEntity[].RecInvInfo[]` | ❌ **错**。就在**表头级**，与 `FEntity`/`FEInvoiceEntity` 平级的独立实体 |

**真正让 7 种写法全失败的是"字段层级放错"**：把 `FRecInv`/`FIVSerialNo` 写到 `Model` 单据头层级
（而不是 `Model.FRecInvInfo[]` 数组行内），金蝶会明确报错：
```
ResolveFiled_InnerEx解析字段(Key:FIVSerialNo,name:发票序列号)时发生异常…实体不存在此属性！
[EntityType：BillHead Propeyt…
```
→ **看到 `[EntityType：BillHead]` 就是层级放错了**，不是锁的问题。

### 4.2.3 实体名映射表（View 用 EntryName / Save 用 Key —— 反复踩的坑）

| 表单 | Save Key | View EntryName |
|---|---|---|
| 费用报销单 `ER_ExpReimbursement` / 差旅费报销单 `ER_ExpReimbursement_Travel` | `FBillHead` | `BillHead` |
| | `FEntity` | `ER_ExpenseReimbEntry` ← 费用明细 |
| | **`FRecInvInfo`** | **`RecInvInfo`** ← 收票信息 |
| | `FEInvoiceEntity` | `FEInvoiceEntity` |
| | `FReimbAndRecInvInfo` | `FReimbAndRecInvInfo` |
| | `FMultiPayeeEntity` / `FSrcEntity` | `FMultiPayeeEntity` / `FSrcEntry` |

**费用申请单 `ER_ExpenseRequest` 没有收票信息实体**（只有 `FBillHead`+`FEntity`），别往它上面写。

### 4.2.4 收票信息实体字段与锁状态（`Key=FRecInvInfo` / 表 `T_ER_RECINVENTRY`）

| 字段 | 中文名 | IsNewLock | IsEditLock | 引用 | 实测可写 |
|---|---|---|---|---|---|
| `FRecInv` | 收票单 | True | True | `IV_RecInvBase` | ✅ **可写（关键发现）** |
| `FISFROMSRCBILL` | 从源单携带 | True | True | — | 系统维护，不用写 |
| `FIVSerialNo` | 发票序列号 | False | False | — | ✅ 可写（但没必要写） |

`FReimbAndRecInvInfo`（明细与发票联动处理关联关系，8 字段全开放）——**不需要填**：
官方单据实测也是 0 行，系统按需自动生成。

### 4.2.5 收票单侧的反查字段（找票时用）

`ExecuteBillQuery formid=IV_ReceivedInvoice`，字段名清单（**逐字段实测通过；名字写错会报
「元数据中标识为 XXX 的字段不存在」，注意 View 里叫 `PDFURL`/`PURNAME`，查询要写 `FPDFURL`/`FPURNAME`**）：

```
FID, FBillNo, FIVNUMBER, FSUMALLAMOUNT, FOPENDATE, FSALENAME, FPURNAME,
FLINKBILLTYPE, FLINKBILLID, FLINKIVNUMBER, FPDFURL, FPIAOZONESERIALNUMBER, FDocumentStatus
```
- 返回列顺序 = `FieldKeys` 顺序（实测三种排列均一致），可安全按位置解析。
- `FLINKBILLTYPE`/`FLINKBILLID`/`FLINKIVNUMBER` = 该收票单当前挂在哪张单上（防抢判据）。
- `FPIAOZONESERIALNUMBER` = 33 位发票云流水号。**注意它 ≠ 发票云的 `fid`**：发票云接口认的是
  `FPDFURL` 尾部那个 **32 位** hash（如 `ac174af76c8df1adc20eacb8aed8e88e`）。
  用 33 位去调发票云 `status/update` 会报 `1301 发票流水号不在该单据下`，用 32 位才返回 `0000`。

### 4.2.6 发票云（piazzone）API 的定位 —— 降级为可选兜底

`helpers/piazzone_tool.py` 仍保留，但**不再是主路径**。仅在"发票不在收票池、需要程序化采集"时才有意义。
其 `cache/save/status` 按官方文档原文只是「**告知发票云**单据与发票的绑定关系」（供发票助手端查询/状态跟踪），
**本身不回写金蝶** —— 这也是当时"接口全返回 0000 但金蝶 0 行"的原因。
出站网络注意：本环境网关按 SNI 拦 `api.piazzone.com`，同 IP 的 `api.piazzone.com` 可通（脚本已内置域名 failover）。

#### 已确认死路（不必再试）
- `ER_ExpReimbursement` 的 18 个操作里**没有**"选择发票"类操作 → `ExecuteOperation` 调按钮不可行。
  ⚠️ **这条的后果比"不可行"严重**：既然官方没有开放"补发票"的 WebAPI 入口，
  用 WebAPI 挂票就只能**直接 Save `FRecInvInfo` 子表**，也就是**跳过发票云的取票与组织归属校验**。
  校验会推迟到界面/审核环节 → 「Save ✅ / Submit ✅ / 界面打开 ❌ → 单据变 D」。
  所以 **`SOURCEORGID`（购方组织）必须等于报销单组织**，且收票单必须是发票云归集的
  （`FPDFURL` / `FPIAOZONESERIALNUMBER` 非空）。详见 `recvin_link.py::guard_recv_invoices()`
  与 SKILL.md「跨组织报销：禁止」。
- `FEntity` 内 `FRecInvBillNo`/`FReimbLinkInvCode`/`FInvNumber` 官方单据也是空的 → 不用填。
- 收票单侧设 `FLINKBILLID` 后重存报销单 → 不会自动回填收票信息（方向反了；正确方向是写报销单侧）。

> 结论均已在本手册内，无需外部资料即可复现。

### 4.3 往来单位 FCONTACTUNIT（坑）

- 类型字段 Save 键为 **`FCONTACTUNITTYPE`** = `"BD_Empinfo"`
- 引用字段 Save 键为 **`FCONTACTUNIT`** = `{"FNumber":"123"}`（员工号）或 `{"Id":132889}`；`FSTAFFNUMBER` 不行。
- ⚠️ **View 显示为 `CONTACTUNIT`/`CONTACTUNITTYPE`（无 F 前缀），但 Save 键必须带 F**
  —— 元数据实测 `FCONTACTUNITTYPE`/`FCONTACTUNIT` 均 `MustInput=1`、未锁。
  用 View 里的名字去查 `FCONTACTUNIT` 会得到 `None`，极易误判成"这字段不存在"。
- 可用**局部更新**：`NeedUpDateFields=["FCONTACTUNIT","FCONTACTUNITTYPE"]`，Model 只传这两个 + `FID`。
- **规律：`FCONTACTUNIT` = 申请人本人**。官方单据实测一致：
  100004 `ProposerID=100175(周九/009)` → `CONTACTUNIT=100175/009`；100003 `100226(赵六/060)` → `100226/060`。
- ⚠️ **修正旧结论**：本环境下推生成的报销单 `FCONTACTUNIT` 为空，但**空着也能 Submit 成功**
  （2026-09-14 实测，Submit 未做该校验）。→ 它**不是硬性前置**，但单据不完整，建议仍补上。
- 工具：`recvin_link.py set-contact <FID> <员工号>`

### 4.4 提交 Submit ✅ 已端到端实测走通（2026-09-14）

```python
POST .../DynamicFormService.Submit.common.kdsvc
{"formid": "ER_ExpReimbursement",
 "data": json.dumps({"CreateOrgId":0, "Numbers":[], "Ids": str(报销单FID),   # ★ Ids 是"字符串"，非数组
                     "SelectedPostId":0, "UseOrgId":0, "NetworkCtrl":"",
                     "IgnoreInterationFlag":""})}
```

**实测结果**：FID 100001 / FYBX20260101000001，`IsSuccess=True`、`MsgCode=0`，
状态 **A(暂存) → B(已提交)**，且**提交后收票信息保持 1 行**（关联未被清掉）。
`SuccessEntitys: [{"Id":"100001","Number":"FYBX20260101000001","DIndex":0}]`

**真实前提（实测修正）**：
1. 收票信息必须已挂有效收票单，且**发票价税合计 ≥ 报销金额** ← **这是硬性的**
2. `FCONTACTUNIT` 往来单位 —— **空着也能提交成功**（不是硬性校验），但建议补
3. 状态为 A 暂存（B/C 也能提交，注意别重复提交）

工具：`recvin_link.py precheck <FID>`（体检）/ `submit <FID>`（有阻断项**硬中止**，v2.0.11 起**无强提交参数**）。

### 4.5 明细局部更新（改金额/费用项目）— ✅ 已实测

明细实体：**View 名 `ER_ExpenseReimbEntry` / Save 键 `FEntity`**（表 `T_ER_ExpenseReimbEntry`）。
改单行用 `NeedUpDateFields` + 分录内码，**不要**整单重写（会把必录的费用项目/部门清空）：

```json
{"NeedUpDateFields":["FExpenseAmount","FExpSubmitAmount","FLocExpSubmitAmount","FLOCNOTAXAMOUNT","FTaxSubmitAmt"],
 "IsDeleteEntry":"false",
 "Model":{"FID":100002,"FEntity":[{"FEntryID":105001,
   "FExpenseAmount":8.00,"FExpSubmitAmount":8.00,"FLocExpSubmitAmount":8.00,
   "FLOCNOTAXAMOUNT":8.00,"FTaxSubmitAmt":8.00}]}}
```

- **必录字段**：`FExpID`(费用项目→BD_Expense)、`FInvoiceType`(发票类型)、`FExpenseDeptEntryID`(费用承担部门→BD_Department)。
  用 `NeedUpDateFields` 就不会碰它们。
- 锁标记字段（`FBorrowAmount`/`FOffsetAmount`/`FRecInvBillNo`/`FReimbLinkInvCode`/`FInvNumber`… `IsNewLock=IsEditLock=True`）
  **实测照样能写** —— 但必须放进 `NeedUpDateFields` 才会生效（本次实测 `FReimbLinkInvCode`/`FRecInvBillNo`/
  `FInvNumber`/`FInvOpenDate` 全部写入成功）。
- ⚠️ **`NeedUpDateFields` 与"新增其它实体的分录行"互斥**：同一次 Save 里带上 `FReimbAndRecInvInfo` 新行
  会被**静默丢弃**。分两次 Save。
- ⚠️ `ValidateFlag="true"` 时 `FCONTACTUNIT` 变必填，先跑 `recvin_link.py set-contact`。

#### 4.5.1 🔴 金额字段勾稽（2026-09-22 实测，101731 六行 + 101464 十二行逐行吻合）

```
含税 ExpenseAmount = 不含税 TaxSubmitAmt + 税额 TaxAmt
                   = LOCNOTAXAMOUNT（不含税）
                   = FTravelAmount（差旅费金额，差旅单专有）
                   = ExpSubmitAmount = LocExpSubmitAmount
```

| View 键 | Save 键 | 含义 | 实例（101731 行1，税率 9%） |
|---|---|---|---|
| `ExpenseAmount` | `FExpenseAmount` | **含税**（价税合计） | 234.0 |
| `TaxAmt` | `FTaxAmt` | 税额 | 19.32 |
| `TaxSubmitAmt` | `FTaxSubmitAmt` | **不含税** ⚠️ 名字反着起 | 214.68 |
| `LOCNOTAXAMOUNT` | `FLOCNOTAXAMOUNT` | **不含税** | 214.68 |
| `FTravelAmount` | `FTravelAmount` | **差旅费金额 = 含税**（**差旅单必写**） | 234.0 |

- ⚠️ **`FTravelAmount` 不是 `FExpTravelAmount`**（旧文档记错，2026-09-22 在 Z 单 101401 上写入验证后订正：
  写 300→301，回读 `FTravelAmount`/`LOCNOTAXAMOUNT` 同步变 301）。
- ⚠️ 旧版 `expense_edit.set_detail()` **整个漏写 `FTravelAmount`**，且把 `FLOCNOTAXAMOUNT` 误写成含税值
  → 差旅单报「差旅费金额不等于税额加费用金额」+ 不含税被污染。**v2.0.9 已修**。
- **差旅单的明细 View 实体名也是 `ER_ExpenseReimbEntry`**（不是 `ER_ExpTravelReimbEntry`，
  也不是 `TravelReimbEntry`），与费用报销单同名。

#### 4.5.2 🔴 金额方向不对称：调低安全、调高受限（2026-09-22 实测）

| 方向 | 结果 |
|---|---|
| **调低**（报销 2474.76 → 发票 1606.76） | ✅ 安全，不踩校验 → 用 `expense_edit.py fit` |
| **调高**（在报销单上加钱） | ❌ 「**关联费用申请金额超出源单已下推报销金额！**」——实测 101464/101404 失败 |

→ 想多报只能回**出差申请单**加额度。`fit` 因此对"上调"一律硬拒。

### 4.8 明细 ↔ 发票联动表 `FReimbAndRecInvInfo`（= UI「合并生成费用明细」的底层）— ✅ 已实测可写

报销单 18 个 Operation 里**没有**「合并生成费用明细」这个可调操作；它只是 UI 选票时的联动动作，
底层落到两处数据。对照真实样本 `FYBX20260101000003`(FID 100010) 复刻：

| 实体 | Key / View 名 | 字段 | 说明 |
|---|---|---|---|
| 联动表 | `FReimbAndRecInvInfo`（两者同名） | `FReimbLinkRecInvCode` | 联动标识（16 位 hex，同一明细行的多条发票分录共用） |
| | | `FRecInvFid` | 收票单内码 |
| | | `FRecInvEntryId` | **收票单分录内码**（= 收票单 View 里 Entity[].Id） |
| | | `FInvAllAmt` / `FInvAmt` / `FInvTaxAmt` | 该分录的 价税合计/不含税/税额（= `TOTALAMOUNT`/`AMOUNT`/`TAXAMOUNT`） |
| | | `FRecInvoiceBillNo` | 收票单编码 |
| | | `FInsurancePremium` | 保险费 |
| 明细侧 | `FEntity` | `FReimbLinkInvCode` | 联动标识，与上表配对 |
| | | `FRecInvBillNo` / `FInvNumber` / `FInvOpenDate` | 收票单号（多个逗号分隔）/ 发票号码 / 开票日期 |

```json
{"FID":100002,"FReimbAndRecInvInfo":[
 {"FReimbLinkRecInvCode":"fd2e35ee05f78c0a","FRecInvFid":118635,"FRecInvEntryId":146292,
  "FInvAllAmt":11.50,"FInvAmt":11.17,"FInvTaxAmt":0.33,"FRecInvoiceBillNo":"SPD00000002","FInsurancePremium":0.0},
 {"FReimbLinkRecInvCode":"fd2e35ee05f78c0a","FRecInvFid":118635,"FRecInvEntryId":146293,
  "FInvAllAmt":-3.50,"FInvAmt":-3.40,"FInvTaxAmt":-0.10,"FRecInvoiceBillNo":"SPD00000002","FInsurancePremium":0.0}]}
```
校验：`Σ FInvAllAmt` 应 = 发票价税合计（本例 11.50 − 3.50 = 8.00 ✅）。
**联动表不是必需**：真实已审核单 100003/100004 就是 0 行，照样过审。别把它当阻断项。

### 4.6 上传发票"生成费用明细"三选项 — 员工提交失败的主因

> ⚠️ **本节是 UI 语义说明，不要与 §4.8 的"联动表"混为一谈**：三个选项决定**要不要新增明细行**；
> §4.8 的联动表只是记录"明细行用了哪几条发票分录"。实测结论（2026-09-14）：下推生成报销单后，
> **只调整已有明细行的金额 + 写联动表**，不新增行 → 提交顺利。

报销单上传发票（无论走发票智慧管家还是收票信息）时，金蝶会要求选择**发票转费用明细的方式**，三选项：

| 选项 | 行为 | 何时用 |
|---|---|---|
| `合并生成费用明细` | 多张发票合并成**一条**费用明细行 | 无下推明细、想按发票汇总 |
| `不合并生成费用明细` | 每张发票生成**各自一条**费用明细行 | 无下推明细、要逐票明细 |
| `不生成费用明细` | **只挂发票做收票信息校验，不新增任何费用明细行** | **报销单由费用申请单下推而来（明细已存在）** |

**关键规则（员工吐槽"选错就提交不了"的根因）**：
- 报销单若由**费用申请单下推**生成，其费用明细行**已从申请单带出**（金额/费用项目/项目/税额齐全）。
- 此时若上传发票选了"生成费用明细"（合并或不合并），会**新增费用明细行**，与下推明细**重复/冲突 → 金额合计错乱 → 校验不通过 → 无法提交**。
- **正确做法：选"不生成费用明细"**，发票仅用于收票信息金额校验，不增行，与下推明细互不冲突，提交成功。

**对 agent 的启示**：替员工做上传决策时，**默认对"下推生成的报销单"采用"不生成费用明细"**；仅当报销单是手工新建（无下推明细）时才按业务需要选合并/不合并。这一步必须由 agent 替员工拍板并用大白话解释，不能把三个选项直接丢给员工。

> 注：本规则适用于金蝶原生上传 UI；若后续走发票云 piazzone API 自动化，需在 API 入参里对应设置"是否生成费用明细"标志，语义一致。
### 4.7 发票云 piazzone「发票助手 PC 端对接」— 服务端绑定发票↔报销单（真正解法）

原生"选择发票"按钮坏掉不要紧：**发票云把该按钮的能力做成了标准 REST API**（发票云标准版，host `api.piazzone.com`）。agent 走这条即可在服务端把发票绑定到报销单并写入 `RecInvInfo`，**无需二开、无需客户端按钮**。

**认证**：`client_id` + `client_secret` + `timestamp` + `sign`；`sign = MD5|SHA256|HMACSHA256(client_id+client_secret+timestamp)`，`encType` 0/1/2（默认 2=HMACSHA256）。V3 接口请求参数**不需加密**（官方《对接约定》明写），无需 encrypt_key。凭证放本地 `helpers/piazzone_config.py`（复制 `piazzone_config.example.py` 后填写），或用 `PIAZZONE_*` 环境变量。

**对接流程（标准版发票助手）**：
1. `getUserKey` `POST /m4/fpzs/getUserKey` — 录入单据信息拿授权。body：`{billNumber, billType:"", bxd_key:报销单ID, ghf_mc:购方名称, tin:税号, eid:申请人ID(uuid), timestamp, client_id, sign, encType, ticketParam:"1101", resource:"5"(星空)}`
2. `getLinkKey` `POST /m4/fpzs/getLinkKey` — 拿回推通道 key
3. 采集发票：发票云页面导入（人工）或电子税务局已入池（自动）→ 回推发票数据（含发票流水号 `serialNo`）
4. **`缓存单据` `POST /m4/fpzs/expense/entry/cache`** ← 服务端绑定关系（确认接口）
   ```json
   {"client_id":"...","sign":"...","timestamp":"...","encType":"2",
    "bxd_key":"报销单ID","eid":"申请人ID",
    "data":[{"entryid":"报销单分录ID(可空)","fid":["发票流水号serialNo", "..."]}]}
   ```
   - 缓存 2 小时；发票云据此把发票关系写回金蝶 `RecInvInfo` 并完成金额校验。
   - `fid` 填的是**发票云发票流水号**（非金蝶收票单 FID）；电子税务局入池的发票在发票云有 `serialNo`，可直接绑定。
5. `保存单据`（持久化；候选 `POST /m4/fpzs/expense/entry/save` 或金蝶发票云 `kapi/app/rim/message` messageType=`billSave`）— 正式落库并写回金蝶。`缓存单据` 已能写入 `RecInvInfo` 时优先用缓存，持久化再补保存。
6. `query_bill_invoices` `GET /m4/fpzs/bxdInvoices?bxd_key=&client_id=&timestamp=&sign=&ticketParam=11011` — 验证绑定结果。

**与金蝶侧的分工**：发票云负责"建收票单 + 绑定 + 写 RecInvInfo + 金额校验"；金蝶 WebAPI 负责"下推报销单 + 补 FCONTACTUNIT + Submit"。agent 串起两者即全自动闭环。

**实时测试限制**：本执行沙箱无 `api.piazzone.com` 出站网络（DNS 不可达），无法在此跑活体；在能访问 piazzone 的环境运行 `helpers/piazzone_client.py`（已实现上述全部方法，签名自检通过）。


---

## 5. 附件（行程单/补充说明，不参与金额校验）— ✅ 2026-09-14 已实测打通

### 5.1 一步式（推荐）：`AttachmentUpLoad`

`POST /k3cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.AttachmentUpLoad.common.kdsvc`

| 参数 | 必填 | 说明 |
|---|---|---|
| `FileName` | ✅ | 文件名（含扩展名） |
| `FormId` | ✅ | 单据标识，如 `ER_ExpReimbursement` |
| `IsLast` | ✅ | **是否最后一片** —— ⚠️ 见下方大红字 |
| `InterId` | ✅ | 单据内码（报销单 FID） |
| `BillNO` | ✅ | 单据编号（下推草稿单 BillNo 为空时，先 Save 一次拿到单号再传） |
| `SendByte` | ✅ | `Convert.ToBase64String(文件字节流)` |
| `Entrykey` / `EntryInterId` | ⭕ | 单据体附件时**要么都填要么都不填**（单据头附件填 `-1` 或不填） |
| `AliasFileName` | ⭕ | 附件别名 |
| `FileId` | ⭕ | 分片上传时，第二片起必填 |

返回：`{"Result":{"ResponseStatus":{"IsSuccess":true},"FileId":"...","Message":""}}`

### 5.2 ⚠️⚠️ `IsLast` 漏传 = 静默失败（本次踩的最深的坑）

`IsLast` 是**必填布尔**，但在 JSON 里漏传时服务端反序列化默认为 `false`，
于是被当成"非最后分片" → 文件**不提交到文件信息表**。
**接口照样返回 `IsSuccess=true` 并给一个 `FileId`**，看起来完全成功，实际什么都没落库。

- 判断真假的唯一可靠办法：**回环下载比对字节**
  `AttachmentDownLoad.common.kdsvc`，body `{"data":{"FileID":"<FileId>"}}`
  → 返回 `Result.FilePart`(base64) + `FileSize` + `FileName`。
  若返回 `IsSuccess=false` 且 Message 含「**数据库文件信息表中不存在编码为 X 的文件信息**」→ 就是没落库。
- `helpers/expense_edit.py attach` 已内置该回环校验（比 md5），**别只看 IsSuccess**。

### 5.3 两步式（备选）：`UploadFile` + `BOS_Attachment` Save

1. `UploadFile.common.kdsvc`：body `{"data":{"FileName":"x.jpg","IsLast":true,"SendByte":base64}}` → `Result.FileId`
   （同样 `IsLast:true` 必传，否则不落库）
2. `BOS_Attachment` Save：Model `{"FFileId":<FileId>,"FAttachmentName":"x.jpg","FBillType":"ER_ExpReimbursement","FInterID":"报销单FID","FBillNo":"<单号>","FAttachmentSize":<字节>,"FExtName":".jpg","FEntryinterId":"-1","FEntrykey":" ","FaliasFileName":"别名","FCreateTime":"..."}`

### 5.4 ⚠️ 验证章节的更正

- `ExecuteBillQuery` 查 `BOS_Attachment` **会报「数据库执行异常，请联系系统管理员」（MsgCode=6）**
  —— 它不是普通可查业务对象。**别再用这个方法验证附件**，改走 §5.2 的回环下载。
- `BOS_Attachment` 元数据（`QueryBusinessInfo`）字段名可读，供 §5.3 用：
  `FInterID`/`FEntryInterID`/`FBillNo`/`FBillType`/`FAttachmentName`/`FAttachmentSize`/`FExtName`/`FFileId`/`FCreateTime`/`FCreateMen`/`FaliasFileName`。
- 以下端点**确认不存在**（别浪费时间猜）：`FileUploadService`、`GetAttachmentList`、
  `DownloadFile`、`GetFileInfo`、`Kingdee.BOS.ServiceFacade.ServicesStub.Attachment.AttachmentService.*`。

### 5.5 探接口契约的通用技巧

金蝶 WebAPI 对缺参数会明确回「data数据包中的参数 **X** 必传」。**发空包读报错，逐轮补参**，
比翻文档快且准（本次 `AttachmentUpLoad` 的 5 个必填项就是这么问出来的）。

---

## 6. 出差申请单 → 差旅费报销单（2026-09-14 申请单环节已实测走通）

- 出差申请单 `ER_ExpenseRequest_Travel` → 下推 → 差旅费报销单 `ER_ExpReimbursement_Travel`
- `ER_ExpReimbursement_Travel` 的**实体结构与费用报销单完全同构**（已用 QueryBusinessInfo 核对）：
  `FBillHead` / `FEntity`(EntryName=`ER_ExpenseReimbEntry`) / `FEInvoiceEntity` / `FRecInvInfo`(=`RecInvInfo`) /
  `FMultiPayeeEntity` / `FSrcEntity` / `FReimbAndRecInvInfo` —— **收票信息、联动表的写法与费用线一模一样**。
- 两个表单的可用操作一致（含 `Push`/`Submit`/`Audit`）。

### 6.1 出差申请单 Save 模板（已实测成功）

```json
{"FID":0,
 "FBillTypeID":{"FNumber":"CCSQ001_SYS"},           // 出差申请单
 "FDate":"2026-09-14",
 "FOrgID":{"FNumber":"101"},
 "FStaffID":{"FSTAFFNUMBER":"123"},                 // ⚠ 用 FSTAFFNUMBER，不是 FNumber
 "FDeptID":{"FNumber":"BM03"},
 "FReason":"…",
 "FCurrencyID":{"FNumber":"PRE001"},
 "FExchangeTypeID":{"FNumber":"HLTX01_SYS"},
 "FLocCurrencyID":{"FNumber":"PRE001"},
 "FExchangeRate":1,
 "FTOCONTACTUNITTYPE":"BD_Empinfo",
 "FTOCONTACTUNIT":{"FNumber":"123"},
 "FCostOrgID":{"FNumber":"101"},
 "FPayOrgID":{"FNumber":"101"},
 "FIsBorrow":"false",
 "FEntity":[{
   "FExpenseItemID":{"FNumber":"001"},              // 001 差旅费（另有 025 市内交通费 / 005 差旅费-市内交通费）
   "FOrgAmount":10,"FLocAmount":10,
   "FEntryCostDeptID":{"FNumber":"BM03"},
   "F_TJKT_Base_qtr":{"FNumber":"ZT00"},            // 项目：植体公用（BAS_PreBaseDataOne）
   "FTravelStartDate":"2026-09-14",
   "FTravelEndDate":"2026-09-14",
   "FTravelStartSite":"北京市","FTravelEndSite":"北京市",
   "FAirTicketCost":0,"FLocalCost":10,              // 明细金额 = 各分项之和
   "FDepartureID":{"FNumber":"04257"},              // 出发地（费控）ER_AreaInfo
   "FArrivalID":{"FNumber":"04257"},                // 目的地（费控）ER_AreaInfo
   "FRemark":"…"}]}
```

### 6.2 出差申请单的坑（实测）

| 坑 | 说明 |
|---|---|
| **单据类型编码** | `CCSQ001_SYS`。⚠️ `BOS_BillType` **不能用 ExecuteBillQuery 查**（报「数据库执行异常」），要从历史单据 View 的 `BillTypeID.Number` 里读 |
| **`FDepartureID`/`FArrivalID` 必录** | 元数据 `MustInput` **没标**，但 `ValidateFlag=true` 会拦：「【出发地（费控）】字段必录」。基础资料 `ER_AreaInfo`（北京市 = `04257`） |
| **基础资料引用格式** | `ER_AreaInfo` 必须用 `{"FNumber":"04257"}`；用 `{"Id":118756}` **不生效**（静默忽略 → 仍报必录） |
| **`ER_AreaInfo` 查询字段名** | `ExecuteBillQuery` 传 `FName`/`FID` 都报「字段不存在」。要么用 View 拿，要么直接照抄已知 FNumber |
| **`FStaffID` 引用** | 用 `{"FSTAFFNUMBER":"123"}`（员工号的键名是 `FSTAFFNUMBER`，不是 `FNumber`） |
| **往来单位键名（又一例 F 前缀差异）** | **View 键 = `TOCONTACTUNITTYPE` / `TOCONTACTUNIT`（无 F）**，而 **Save 键 = `FTOCONTACTUNITTYPE` / `FTOCONTACTUNIT`**。按 View 键名回写会失败 |
| **B(已提交) 仍可改** | 实测在 B 状态用 `NeedUpDateFields:["FReason"]` / `["FRemark"]` 改事由与明细备注均成功 |
| **明细金额构成** | `FOrgAmount`/`FLocAmount` = `FAirTicketCost + FOtherRemoteCost + FLocalCost + FAccomFee + FOtherExpense + FTravelSubsidy` |

### 6.3 待实测

- 出差申请单 → **下推**差旅费报销单（`TargetFormId=ER_ExpReimbursement_Travel`）
- 差旅费报销单的收票信息挂票、明细对齐、附件 —— 预期与费用线同构，但**未实跑**。
