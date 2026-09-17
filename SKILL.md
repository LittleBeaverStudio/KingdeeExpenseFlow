---
name: kingdee-expense-flow
slug: kingdee-expense-flow
displayName: 金蝶云星空报销助手
version: 2.0.4
summary: 小河狸工作室出品：金蝶云星空费用/差旅报销全流程提交，员工不登录金蝶也能报销。
description: 小河狸工作室出品。金蝶云星空「费用/差旅报销全流程」提交 Skill：员工无需登录金蝶，在对话里把发票交上来，agent 用纯 WebAPI 完成「费用申请单 → 下推报销单 → 挂收票信息／传附件 → 提交审批」，差旅线结构同构。内置发票 OCR 清晰度与抬头校验、收票信息 vs 附件分流（行程单双算预警）、跨组织挂票与发票云流水号拦截、提交前体检、报销制度提醒。
license: 小河狸非转售许可 1.0（企业内部使用免费，转售收费需授权）
tags: [金蝶云星空, 费用报销, 差旅报销, 财务自动化, WebAPI, 发票]
metadata:
  version: 2.0.4
  author: 小河狸工作室
  tags:
    - 金蝶云星空
    - 费用报销
    - 差旅报销
    - 财务自动化
    - WebAPI
    - 发票
---

## 许可与使用范围

本 Skill 采用「小河狸工作室非转售许可 1.0」，完整条款见本目录 `LICENSE` 文件。

- ✅ **免费使用**：
  - 个人学习、研究或个人事务；
  - **企业内部使用**——某一组织（无论是否为营利单位）为**其自身**业务运营而使用，包括内部办公、内部财务与会计处理、内部管理与报表、内部系统集成；同一控股关联实体使用视同内部使用。
- ✅ 可为上述用途自行修改本 Skill。
- ❌ **须另行取得书面授权**：对外销售、出租、许可；集成进对外收费的产品或服务；托管 / SaaS / 代运营；打包进收费课程、付费社群或付费安装包；收费的实施与咨询培训；**以及利用本 Skill 向任何第三方交付成果或提供服务**（含代理记账、为客户提供账目或报表服务等）。
- 📌 **区分标准只有一条**：**是否以本 Skill 向本组织之外的第三方交付成果或提供服务**。仅为自身业务使用 → 免费；向第三方交付或服务 → 需授权。
- 📌 再分发须保留本许可、版权声明与来源：小河狸工作室 LittleBeaverStudio · https://littlebeaver.top

**商业授权申请**：https://littlebeaver.top （邮箱 yk.niu@outlook.com）


> ⚠️ 字段说明：`slug` / `displayName` / `version` / `summary` / `tags` 是 **SkillHub（skillhub.cn）发布**要求的顶层字段
> （CLI 校验必须有 `slug`+`version`+`displayName`，否则 `skillhub publish` 直接 die）；
> `name` + `metadata` 是 **agentskills** 规范要求的。两套并存，互不影响。

# 金蝶云星空报销助手

> 小河狸工作室出品 ｜ 覆盖「费用 / 差旅」两条报销线的**全流程提交**：申请单 → 下推报销单 → 挂发票 → 提交审批。

> **关于平台「需配置 API Key」标签**：本 Skill **不需要申请任何 API Key**。
> 它使用**你自己的金蝶云星空账号**提交单据，只填一次连接信息即可；
> 若已安装 `kingdee-data-exporter`，**两者共用同一份配置，不需要重复填写**，
> 也不必去任何开放平台申请密钥。只填**账套名称**即可，账套 ID 会自动解析。
> 配置方法与自检见下方「安装与配置」。

本 skill 处理**写操作**（提交单据、下推、创建收票单、上传附件、提交审批），与只读导出的 `kingdee-data-exporter` 互补。

## 安装与配置

### 1. 依赖

```bash
python -m pip install -r requirements.txt
```

本 skill 的 `helpers/` 全部是**纯标准库**（含 `recvin_link.py`），核心写票功能无需第三方包。

### 2. 连接配置：通常**不需要你做任何事**

本 skill **不单独保存账号密码**，也没有独立的配置项。只要机器上已经装好并配置过
[`kingdee-data-exporter`](https://github.com/LittleBeaverStudio/KingdeeDataExporter)，
本 skill 会**自动复用它的连接配置**，不需要重复填写。

确认是否已就绪 —— 在导出技能目录执行（五步全绿即可）：

```bash
python data_exporter.py --doctor
```

> 为什么本 skill 自己也要读一次凭据？因为它要做**写操作**（提交单据、下推、挂传附件），
> 而导出技能是**只读**的、不提供写接口。所以连接动作必须自己完成，
> 但读的是**同一份配置**，用户不需要多填任何东西。

### 3. 只有这几种情况才需要自己配

| 情况 | 做法 |
|---|---|
| 没装导出技能，想单独用本 skill | 写 `~/.workbuddy/kingdee/config.json`（见下方），只填账套名称即可 |
| CI / 无人值守环境 | 用环境变量 `KINGDEE_BASE_URL` / `KINGDEE_ACCTID`（或 `KINGDEE_ACCT_NAME`）/ `KINGDEE_USERNAME` / `KINGDEE_PASSWORD` |
| 已经在用导出技能的 `config.py` 旧写法 | **无需操作**，本 skill 同样兼容 |

自己配的话，写这个（`~/.workbuddy/kingdee/config.json`，技能目录之外，升级不覆盖）：

```json
{
  "base_url": "https://你的域名/k3cloud/",
  "acct_name": "账套名称",
  "username": "集成账号",
  "password": "密码"
}
```

不需要自己找账套 ID —— 只填账套名称，脚本会自动解析；想列出来看看就跑一次
（这个接口**免账号密码**）：

```bash
python data_exporter.py --list-datacenters
```

### 4. 连不上时

优先跑导出技能的 `--doctor`，它会逐步定位是配置、网络、账套、登录还是权限问题。

**WebAPI 白名单未配**是最常见的（报 `MsgCode 11`，**与账号密码无关**）：
让金蝶管理员在「基础管理 → 公共设置 → 参数设置 → 基础管理 → BOS平台 → WebAPI →
「允许调用WebAPI接口用户」」加入取数账号。

> ⚠️ 密码连续错约 5 次会锁账号，本 skill 不做自动重试。

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
     python helpers/expense_edit.py linkage <FID> --recv SPD00000002 --entry <明细分录内码>
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

## 🔴 跨组织报销：**禁止**（2026-09-15 实测，两次结论修正后的定稿）

**问题**：能不能把 A 组织的发票挂到 B 组织的报销单上？（例：购方「示例科技」的票，给「示例四科技」/「示例二科技」报销）

**结论：不能。而且是"能写进去、单据却不可用"的那种不能。**

实测三轮，结论逐轮修正（过程留档，避免再走回头路）：

| 轮次 | 看到的现象 | 当时的错误结论 |
|---|---|---|
| ① | `Save` 写 `FRecInvInfo` 成功、收票单联动回写 | "能挂" |
| ② | `Submit` 也成功，当场读到 `B` | "接口完全接受" |
| ③ | 随后变 `D`；**界面点「查看发票」直接报错** | ✅ **真结论：不可用** |

**界面上真实报的错**（用户实测截图）：
```
驳回：无法获取当前关联收票单的发票云发票流水号，请尝试删除收票单后重做收票
```

**为什么能写进去 —— 我走的是官方没提供的那条路，不是"绕过后门"**

1. **金蝶 WebAPI 根本没有"补发票"这个 Operation**（`ER_ExpReimbursement` 的 18 个 Operation 里没有）。
   发票云取票 + 回写流水号是**纯 UI 交互**，WebAPI 没开放。
2. 所以 WebAPI 挂票只能**直接 Save `FRecInvInfo` 子表** —— 等于写一行"指向某张收票单"的
   关联记录，**完全跳过发票云的取票与组织归属校验**。
3. 我们的 `Save` 报文还额外把校验关掉了：`ValidateFlag=false` + `IsVerifyBaseDataField=false`
   （`recvin_link.py::save()` 的默认值）→ 连"收票单组织 ≠ 报销单组织"这类字段级校验也不会拦。
4. 于是校验被**推迟到界面打开 / 审核环节**才发生 →
   表现就是「Save ✅ → Submit ✅（B）→ 界面/审核 ❌ → `D`」。

**三条硬约束（`guard_recv_invoices()` 已内置，写入前拦截）**

| # | 约束 | 违反后的表现 |
|---|---|---|
| 1 | 收票单 `SOURCEORGID`（购方/来源组织）**必须 = 报销单组织** | 界面报「无法获取…发票云发票流水号」→ `D` |
| 2 | 收票单必须是**发票云归集**的（`FPDFURL` / `FPIAOZONESERIALNUMBER` 非空） | 同上（同组织也会报） |
| 3 | 目标组织**收票服务许可**须在有效期内 | 发票云报「…【收票服务】许可已过期失效…[0300]」 |

本次两张单各踩了不同的坑，正好把三条都验证到：

| 报销单 | 组织 | 收票单 | 组织归属 | 发票云流水号 | 实际报错 |
|---|---|---|---|---|---|
| 100005 | 104 | SPD00000002 | ❌ 属 101 | ❌ **空**（非发票云归集） | 「无法获取…发票云发票流水号」 |
| 100006 | 105 | SPD00000001 | ❌ 属 101 | ✅ 有（`GENERATETYPE=4`） | 「【收票服务】许可已过期失效 [0300]」 |

→ 两张单的失败**都可以归到"票不是本组织的"**：
第 3 条（105 许可过期）是**叠加**上去的独立故障 —— 就算票是 105 自己的，105 现在也收不了票。

**⚠️ 挂票会改写收票单，撤销时要还原**

收票单有 `SOURCEORGID`（来源/购方组织）与 `SETTLEORGID`（结算组织）。挂票后
`SETTLEORGID` 会被**改成目标报销单的组织**，同时 `LINKBILLID/LINKIVNUMBER/LINKBILLTYPE`
回写指向那张报销单：

| 收票单 | SOURCEORGID / PURNAME | SETTLEORGID 原 | 被挂后 | LINK 字段 |
|---|---|---|---|---|
| SPD00000002 | 101 / 示例科技有限公司 | 101 | **104** | → 100005 / FYBX…0001 |
| SPD00000001 | 101 / 示例科技有限公司 | 101 | **105** | → 100006 / FYBX…0002 |

→ **挂错了要还原：`SETTLEORGID` 改回原组织 + 清掉 LINK 三件套**，
否则这张票在原组织的收票核算里也是错的。

### ⚠️「没有购买发票模块」≠ 收票信息不可用（此结论仍成立，但它不是"能挂"的理由）
示例二科技（org 105）据称未购买发票模块，但：
- 收票信息写入 **成功**（1 行，无任何报错），提交 **A→B 也成功**
- 收票单在 `CreateOrgId` = 0 / 101 / 104 / 105 下**均可见**，没有可见性拦截
- **反向证据**：org 105 名下（`SOURCEORGID=105`）有 **701 张**收票单，
  `SETTLEORGID=105` 的有 542 张 → 105 本身就有大量收票单，"没有发票模块"在**收票单层面不成立**

**正确理解**：发票模块影响的是**发票自动归集**（发票云把票自动收进收票池 → 自动生成收票单），
**不是**报销单「收票信息」这个子表本身。所以：
- 没有发票模块的组织 → 它的发票**不会自动进收票池**，需要人工建收票单或用附件
- 但"能挂上"有前提：**收票单必须是本组织的**（见上面三条硬约束）。
  ~~哪怕是别的组织建的也能挂~~ —— 这句只对 `Save` 成立，对**单据可用性**不成立。

### 🔑 那为什么"示例二科技要走附件"是对的？—— 是**业务做法**，不是系统限制
历史单据实测（2026-09-15）：

| 报销单 | 组织 | 状态 | 收票信息行数 |
|---|---|---|---|
| 历史单A (100007) | **105 示例二科技** | C 已审核 | **0 行** |
| 历史单B (100009) | 104 示例四科技 | B 审核中 | 1 行 |
| FYBX20260101000002 (100004) | 101 示例科技 | C 已审核 | 2 行 |

→ **org 105 的历史报销单根本不用收票信息**（0 行照样审核通过），org 104/101 才用。

**结论**：
- 技术层面「能挂」≠ 业务层面「该挂」。
- 对 **105 这类不用收票信息的组织**，应**跟随其既有做法走附件**，
  硬挂收票信息反而会与历史单据口径不一致（且会把来源组织的收票单改写成 105 结算）。
- skill 的默认动作：**先查该组织历史报销单用不用收票信息**，再决定走「收票信息」还是「附件」。

### 🔴 硬拦截：`发票购方名称 ≠ 报销单组织名称` ＝ **不许挂**
这是**税务归属**问题（谁的进项、谁抵扣），而且系统在界面/审核环节也会挡。
实测两张票购方都是「示例科技有限公司」，而报销组织是「示例四科技有限公司」/「示例二科技有限公司」。
→ **这是阻断项，不是"提醒一下再确认"**。`recvin_link.py link / precheck` 已经默认拦截；
   要用 `--allow-cross-org` 才能越过，越过就是造一张会被驳回的单。

### ✅ 拦截落在两道口子上（2026-09-15 补测）
```
recvin_link.py link    <FID> <收票单号…>     → 写入前就拦（避免产生坏数据）
recvin_link.py submit  <FID>                 → 提交前再拦一次（cmd_submit 先跑 precheck）
```
- `link` 拦截点：`guard_recv_invoices()`，**在 Save 之前** raise，**不会写库**。
- `submit` 拦截点：`cmd_submit` → `precheck()`，**有阻断项直接 return 2，连 `Submit` 接口都不调**。
  实测 `submit 100005` 输出「⛔ 体检有阻断项，已中止（确认要强提交请加 `--force`）」并列出
  跨组织 + 无发票云流水号两条，**没有发起任何提交**。
- ⚠️ 也就是说：**正常走 skill 的路径，这种单现在已经"提交不过去"了**。
  唯一能绕过的是显式 `--force` / `--allow-cross-org`。

### ⚠️ 别把「收票信息为空」当阻断项（2026-09-15 修正）
曾经把它列为 blocks，理由是"大概率报发票金额不允许小于报销金额"——**实测推翻**：
`100007`（org 105）**报销金额 金额以实际单据为准、收票信息 0 行**，照样 Submit 成功并走到 `C` 已审核。
→ 已降级为 warn。否则会把 org 105 这类"走附件"的正常单据误拦。

### 📌 提交后的状态流转（D 的真正成因已查明）
- `Submit` 返回成功，**当场读到的状态 = B（审核中），且收票信息保持** ✅
- ⚠️ 但随后再读，两张单都变成了 **`D`**，且 `APPROVERID` / `APPROVEDATE` 为空。
- **`D` 的权威含义 = 「审核驳回」**（金蝶官方《单据状态通用说明》）：
  > 「重新审核：用户执行业务审核**驳回**操作时单据状态更新为重新审核，表示当前单据审核**不通过**，需要重新审核。」
  > —— https://open.kingdee.com/K3Cloud/help/PUR_NewTopic43.html
  - 该状态**类同「创建」**：可以修改、可以删除、可以再次提交；但不可下推、不被其他单据引用。
  - 另一处口径「已审核后被反审核」在本次场景**不适用**（这两张单从未到过 `C`）。
- **成因不是"人工审批不通过"，而是发票云取不到这张票的流水号**。
  ⚠️ 触发点要写准（用户 2026-09-15 澄清）：**报错不是在"审核"动作里出现的，而是在
  「单据被驳回后、在收票信息页签重新上传/补发票」时出现的**：
  `驳回：无法获取当前关联收票单的发票云发票流水号，请尝试删除收票单后重做收票`。
  也就是说 —— 只要界面要按**本组织税号**去发票云取这张票（打开发票、补发票、重新上传、
  后续审核/结算），都会取不到。`D` 同理：金蝶把"操作被拒绝"写作"驳回"，
  所以 `APPROVERID` 一直是空的 —— **根本没有审批人参与**。
- **全环境基线**（2026-09-15 取 300 张报销单）：`C`×297、`B`×1（100009 长期卡住）、
  `D`×2（正好就是本次跨组织测试的 100005 / 100006）。
  → **跨组织挂票是本环境唯一出现 `D` 的单**，其余走 org 自己的票都能到 `C`。

**所以真正的风险落点不是「提交失败」，而是「提交成功、但单据在界面里没法继续走」**：
- WebAPI 层面的硬校验（金额 ≤ 发票价税合计、联动表合计、往来单位）**全部通过**，
  `Submit` 照常回 `IsSuccess=true` —— **接口不会替你挡这个错**。
- 因此拦截必须**由 skill 自己做**（见上「拦截落在两道口子上」），不能指望金蝶接口返回失败。
- 提示原文/流程节点**只在 UI 里看得到，WebAPI 读不到**（`WF_*` / `BOS_OperationLog`
  等表单均不存在，别去猜）。
- 因此遇到 `D` **必须让用户去 UI 看提示原文**，**不要自行重提交**、更不要靠重试硬撞。
- 状态口径参考：`Z` 暂存 / `A` 创建 / `B` 审核中 / `C` 已审核 / `D` 重新审核（驳回）

### ⚠️ 两条复核项（本次实测顺带发现）

1. **同一张发票不要既挂「收票信息」又传「附件」**（100006 就是这种情况：收票信息 1 行
   `SPD00000001` + 同一张票的图片附件）。两个入口都会被财务看到，口径上算 **重复佐证**，
   应二选一。默认：**能挂收票信息就只挂收票信息**；只有该组织既有做法是走附件（如 org 105）才走附件。
2. **收票单上的联查号可能是「悬空号」**：`SPD00000002` 的 `FLINKIVNUMBER = CLFBX00000001`，
   但该差旅报销单**已不存在**（某次下推占号后未保存/被删）。
   → 挂票前若看到 `FLINKIVNUMBER` 有值，**先按单号反查该单是否真的存在**，
   别把它当成"这张票已经被用掉了"而误判。
   （反向也成立：反查单据用 `ExecuteBillQuery` + `FBillNo='...'` 精确匹配，
   返回空数组 `[]` 就是不存在，不会报错。）

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
- **`FIVSerialNo`(发票序列号) 不必写**：官方流程产物的 `FIVSERIALNO` 实测是空的（100003/100004 均为 `" "`），
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
  对照真实样本 `FYBX20260101000003`(FID 100010) 复刻结构最稳。
- **联动表不是必需的**：真实已审核单 100003/100004 的 `FReimbAndRecInvInfo` 就是 0 行，照样审核通过。
  只有走过 UI「联动处理」的单才有（如 100010 有 5 行）。所以别把"联动 0 行"当成提交阻断项。

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
