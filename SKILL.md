---
name: kingdee-expense-flow
slug: kingdee-expense-flow
displayName: 金蝶云星空报销助手
version: 2.3.2
summary: 小河狸工作室出品：金蝶云星空费用/差旅报销全流程提交，员工不登录金蝶也能报销。
description: 小河狸工作室出品。金蝶云星空「费用/差旅报销全流程」提交 Skill：员工无需登录金蝶，在对话里把发票交上来，agent 用纯 WebAPI 完成「费用申请单 → 下推报销单 → 挂收票信息／传附件 → 提交审批」，差旅线结构同构。内置发票清晰度与抬头校验、收票信息 vs 附件分流（行程单双算预警）、跨组织挂票硬拦截、收票池账号可见性隔离、四条提交硬闸门（收票 0 行 / 无发票云流水号 / 跨组织 / 发票合计 < 报销额，无绕过参数）、按发票金额调低报销额（fit，唯一合规出路）、服务端上传发票并建收票单（员工零操作）、单一登录入口、提交前体检。适用于金蝶云星空（K3 Cloud）的费用报销、差旅报销、员工报销、发票挂单、提交审批场景。
license: 小河狸非转售许可 1.0（企业内部使用免费，转售收费需授权）
tags: [金蝶云星空, 费用报销, 差旅报销, 财务自动化, WebAPI, 发票]
metadata:
  version: 2.3.2
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

- ✅ **免费使用**：个人学习/研究/个人事务；**企业内部使用**（为自身业务运营而使用，含内部财务与会计处理、
  内部系统集成）；同一控股关联实体使用视同内部使用。可为上述用途自行修改。
- ❌ **须另行取得书面授权**：对外销售、出租、许可；集成进对外收费的产品或服务；托管 / SaaS / 代运营；
  打包进收费课程、付费社群或付费安装包；收费的实施与咨询培训；**以及利用本 Skill 向本组织之外的第三方
  交付成果或提供服务**（含代理记账、代客出账等）。
- 📌 区分标准只有一条：**是否以本 Skill 向本组织之外的第三方交付成果或提供服务**。
- 📌 再分发须保留本许可、版权声明与来源：小河狸工作室 LittleBeaverStudio · https://littlebeaver.top
  商业授权申请：https://littlebeaver.top （yk.niu@outlook.com）

> `slug` / `displayName` / `version` / `summary` / `tags` 是 SkillHub 发布要求的顶层字段；
> `name` + `metadata` 是 agentskills 规范要求的。两套并存。

# 金蝶云星空报销助手

覆盖「费用 / 差旅」两条报销线的**全流程提交**：申请单 → 下推报销单 → 挂发票 → 提交审批。

- **本 skill 做写操作**（提交、下推、建收票单、传附件）；只读导出由 `kingdee-data-exporter` 负责，
  两者**共用同一份连接配置**。
- **不需要任何 API Key**：用你自己的金蝶账号登录。客户只需维护 3 项：公司名（账套名称）、用户名、密码，
  账套 ID 自动解析。
- 全部 `helpers/` 为**纯标准库**，无需第三方包。

## 1. 安装与配置

```bash
python -m pip install -r requirements.txt    # 仅 Excel/分析类辅助用到，核心写票零依赖
```

### 1.1 配置连接（推荐向导，明文不落盘）

```bash
python helpers/setup_credentials.py          # 弹窗填 公司名 / 用户名 / 密码
python helpers/setup_credentials.py --check  # 只看状态，不开窗
```

凭据用 **Windows DPAPI** 加密存到 `~/.workbuddy/kingdee/credentials.bin`：绑定当前 Windows 用户，
同机其他用户读不出、拷到别的机器解不开、不在技能目录内（升级/打包不会带上）。

**不用要求用户先装 Python 或 tkinter** —— 向导自己找路，全程零安装：

| 层 | 行为 | 用户感受 |
|---|---|---|
| L0 | 当前解释器带 tkinter → 直接弹窗 | 无感 |
| L1 | 没带 → 在本机找**已装的、带 tkinter 的另一个 Python**（py 启动器 / 注册表 / 安装目录），由它弹窗 | 无感，**不改系统 PATH** |
| L2 | 一个都没有 → 自动转**文字向导**（独立新控制台窗口，密码走那个窗口自己的 stdin） | 一个黑窗口，能填 |
| L3 | 都不行 → 如实告知，给"用户点确认后我才协助装"的可选动作 | 明确，不猜 |

> 🚫 **禁止静默安装**任何运行时（Python / tkinter / .NET 组件）。
> 理由：tkinter 不是 pip 包，装它等于**替换用户的 Python**；UAC、企业策略、离线、安全软件任一环节都能让它失败，
> 并留下半装状态 —— 比"没装"更难诊断。要装必须用户明确同意，且装到用户目录、不接管默认 `python`。

**退出码（按码判断结果，不要看有没有报错、也不要看输出文字）**

| 码 | 含义 | agent 该做什么 |
|---|---|---|
| `0` | 已加密保存 | 继续原命令 |
| `10` | 用户取消 / 关窗 / 20 分钟无操作自动关闭 | **没改成**：不要重跑；问一句"要不要再开一次"。注意此时若用户已改掉金蝶后台密码，本地仍是旧密码，**盲目重跑等于撞锁号** |
| `12` | 已经有一个配置窗口开着 | 让用户在**那个**窗口填完，别开第二个 |
| `2` | 环境不支持（非 Windows / 无 DPAPI / 界面全不可用） | 如实告知，给可选方案 |
| `3` | 保存失败（窗口里已提示原因） | 把原因念给用户 |

窗口本身的防错设计：有「显示」勾选可核对密码；`--change-key` 模式要求**输两次**（打错的代价是必然登录失败 + 撞锁号计数）；
关窗前若有未保存内容会再确认一次；同一时间只允许一个配置窗口。

> ⛔ **agent 行为约束**
> 1. **绝不**要求用户在对话里提供金蝶/发票云的账号、密码或密钥 —— 让用户自己在弹窗里填。
> 2. **绝不**代替用户把读到/听说的凭据写进任何文件（含明文配置）。历史上曾发生 agent 从截图读出凭据
>    后落盘成 `piazzone_config.py`，这属于不该有的行为。
> 3. 需要凭据时，正确动作是**提示用户运行向导**，再用 `--check` 确认就绪（只回报字段名，不回显值）。

**为什么不复用导出技能的登录**：导出技能是只读的，不提供写接口；本 skill 必须自己登录，
但读的是**同一份配置**，用户不需要多填任何东西。

### 1.2 明文配置会被强制阻断

只要本机存在**含密码的明文** `~/.workbuddy/kingdee/config.json`，本技能（及导出/分析技能）
会**直接拒绝取数/写单**并打印迁移指引 —— 软提示等于没有：明文还能用，就永远没人去迁移。

迁移是**三件事，互不替代**：

| 步骤 | 解决的风险 |
|---|---|
| ① 到金蝶后台**改掉该账号密码** | 「**已经明文过**」—— 文件能删，但"曾被明文保存"撤不回来（备份/云同步可能已带走）。改密码让旧密码作废 |
| ② 用**新密码**在向导里重新录入 | 让工具改用一份从未明文过的密码 |
| ③ 向导保存成功后**自动删除**明文文件 | 「**以后**」—— 本机磁盘不再留明文 |

> ⚠️ **删除 ≠ 撤销**：钥匙掉在路上被捡过，把钥匙收好不能让对方手里的配钥匙作废，得换锁。
> 过渡期确有自动化必须沿用明文，可显式设 `KINGDEE_ALLOW_PLAINTEXT=1`（会打印告警），请尽快迁走。

**被阻断时 agent 怎么做**（不要把命令丢给用户自己跑）：

1. 念给用户：「检测到你本机的金蝶密码是明文存的。建议先到金蝶后台把这个账号的密码改掉 ——
   文件能删，但『曾被明文保存』这件事撤不回来。改完跟我说一声『**需要修改密钥**』，
   我给你弹个窗口录新密码，保存后旧的明文文件会自动删掉。」
   若用户问"既然会自动删，还改密码干嘛"，按上表回答：**改密码管"已经明文过"，删文件管"以后"**。
2. 用户说「需要修改密钥 / 改密码」时，**由你在后台运行** `python helpers/setup_credentials.py --change-key`
   （窗口弹在用户屏幕上，**不要阻塞等待**）。
3. 用户说填完了 → `--check` 确认，看到「就绪状态 : ✅ 已用加密凭据，闸门放行」即可重跑原命令。
4. 脚本退出码 `10` = 用户取消/关窗（**没改成**）→ 不要重跑业务命令，问一句"要不要再开一次窗口"；
   `12` = 已经有一个配置窗开着 → 让他在那个窗填完；`2` = 环境不支持 → 按上表如实告知。

⛔ **绝不能**：让用户自己打开 `.py` 文件；让用户在对话里说出密码；替用户把密码写进任何文件。

### 1.3 其它配置方式

**推荐顺序就是下面这个顺序（先保住"弹窗/向导"这条路，环境变量永远排最后）**：

| 方式 | 做法 | 边界 |
|---|---|---|
| 配置向导（首选） | `python helpers/setup_credentials.py` | 普通用户，明文不落盘。缺 tkinter 会自动换解释器 → 再转文字向导，**不需要用户装东西** |
| 文字向导（自动降级） | `python helpers/setup_credentials.py --console` | 无图形界面机器；独立控制台窗口录入，密码不进对话记录 |
| 环境变量 | `KINGDEE_BASE_URL` / `KINGDEE_ACCTID`（或 `KINGDEE_ACCT_NAME`）/ `KINGDEE_USERNAME` / `KINGDEE_PASSWORD` | **仅** CI / 无人值守。**本地并不保密**（同用户其它进程可读、随子进程继承、进崩溃转储） |
| 旧版明文 `config.json` | `{"base_url","acct_name","username","password"}` | 兼容保留但不推荐，会被闸门拦（见 1.2）；读到含密码时打印一次迁移提示，`KINGDEE_SILENCE_MIGRATION_HINT=1` 可静音 |
| 导出技能的 `config.py` | —— | 无需操作，本 skill 同样兼容 |

**读取优先级：环境变量 > 系统加密凭据 > 明文 `config.json` > 导出技能 `config.py`。**
（加密凭据残缺时自动回落下一级，不会把已能用的配置弄坏。）

免账号密码查账套（找 acctid 或核对名称）：

```bash
python data_exporter.py --list-datacenters
```

### 1.4 发票云（可选，仅"验票 / 服务端上传发票"才需要）

> 📌 **agent 提醒规则：用本技能时提醒一次即可，别反复念。**
> 「若贵司已购买金蝶「收票模块」并需要发票验票 / 服务端上传，才需要在向导的「可选：发票云」里维护；
> 否则整组留空忽略，不影响报销主链路。」

- **填在哪**：向导 →「可选：发票云」组，**一家一个框，只填 4 项**：
  `购方企业全称` / `购方税号` / `client_id` / `client_secret`；多家点「＋ 添加一家」。
  与金蝶凭据一起存进同一个 DPAPI 加密文件。（`encrypt_key` 当前用不到。）
- 🔑 **发票云权益按「税号」划分，不是按账套**（官方：「请使用税号对应的企业授权，不要用租户授权」）。
  同一账套下**每家买了收票服务的公司各有一组**，买了几家就加几家。
- ✅ **不用填「组织名」和「对应金蝶组织」**：组织名自动 = 购方企业全称；上传/建票时按报销单的组织名称
  自动选中对应那家（`piazzone_client.select_org(org_name=…)`，简称/带后缀也认）。
- 🔧 **自动路由**：`upload` / `create-recvin` 的 `--org` 可留空（按名称自动匹配）；
  显式传编码时按编码优先。配了多家而两轮都没命中 → `errcode=ORGUNMATCHED` 并**拒绝上传**
  （不猜、不拿别家授权硬跑）。手动指定优先级：`PIAZZONE_ORG` > `--org` / `KINGDEE_ORG` > 名称自动匹配 > `active` > 第一个。
  只配 1 家时不需要路由。
- **域名**：`api.piaozone.com`（唯一正确域名）。早前的 `api.piazzone.com`（两个 z）**不存在**，
  表现为 TLS 重置 —— 不要配 hosts。
- **凭据从哪来**：向「发票云（票总管）」客服索取，或登录 `open-ultimate.piaozone.com` 查看。
  **没有其他自动获取途径**，金蝶侧也不暴露。
  税号 / 企业全称（请求体的 `tin` / `ghf_mc`）在金蝶侧也有：收票单 `IV_ReceivedInvoice` 带
  `FPURTAXNUMBER`（购方税号）与 `FPURNAME`（购方名称），可以不手填。
- ⛔ **不要向用户索要这些密钥**，也不要替用户写进任何明文文件。

### 1.5 连不上时

优先跑导出技能的 `--doctor`（逐步定位配置/网络/账套/登录/权限）。
最常见的是 **WebAPI 白名单未配**（报 `MsgCode 11`，**与账号密码无关**）：让管理员在
「基础管理 → 公共设置 → 参数设置 → 基础管理 → BOS平台 → WebAPI → 允许调用WebAPI接口用户」加入该账号。

> ⚠️ 密码连续错约 5 次会锁账号，本 skill 不做自动重试。

## 2. 四种单据与下推关系

| 单据 | formid | 下推目标 | 状态 |
|---|---|---|---|
| 费用申请单 | `ER_ExpenseRequest` | → 费用报销单 `ER_ExpReimbursement` | ✅ 已跑通 |
| 费用报销单 | `ER_ExpReimbursement` | 终态，提交审批后付款 | ✅ 已跑通 |
| 出差申请单 | `ER_ExpenseRequest_Travel` | → 差旅费报销单 `ER_ExpReimbursement_Travel` | ✅ 创建→提交→审核 已跑通 |
| 差旅费报销单 | `ER_ExpReimbursement_Travel` | 终态 | 🟡 结构与费用单同构；除 Submit 外全链路已通过 |

> ⚠️ formid 拼写极敏感：`ER_ExpReimbursement` ✅ / `ER_ExpenseReimbursement` ❌
> （曾因此误判"单据不存在"）。查不到单据时先怀疑拼写或表单权限，别直接认定"没部署"。

## 3. 三个位置：收票信息 / 附件 / 联动表（最易踩坑）

| 位置 | 单据体或接口 | 用途 | 参与金额校验 |
|---|---|---|---|
| **收票信息** | 报销单 `RecInvInfo` 单据体 → 链接收票单 `IV_ReceivedInvoice` | **上传发票** | ✅ 是 |
| **附件** | `AttachmentUpLoad`（一步挂单）/ `UploadFile`+`BOS_Attachment` | 行程单、补充说明 | ❌ 否 |
| **联动表** | 报销单 `FReimbAndRecInvInfo` 单据体 | 明细 ↔ 发票分录对应（= UI「合并生成费用明细」的底层） | ❌ 否，但决定明细是否"与发票一致" |

- 报错「发票金额不允许小于报销金额」= 收票信息里**没有**有效收票单（发票没传对位置，或被传成了附件）。
- `FEInvoiceEntity` 是另一个发票子表，**不是**收票信息，历史单多为空 —— 不要往这里填发票绕过校验。

## 4. 标准流程（费用申请单 → 报销单）

1. **提交费用申请单**：`Save ER_ExpenseRequest`（模板见 `references/field_cookbook.md`，含项目必填等坑）。
   默认只到暂存，确认后再 Submit。
2. **下推报销单**：`Push ER_ExpenseRequest` → `ER_ExpReimbursement`（`TargetFormId` 必填，启用默认规则）。
   生成草稿（状态 Z，BillNo 空）。
3. **发票先"找"再"建"**（顺序很重要）：
   ```bash
   python helpers/recvin_link.py resolve 24000000000000000001    # 按发票号解析"该用哪张收票单"（有流水号 + 未占用）
   python helpers/recvin_link.py find 24000000000000000001       # 按发票号码查收票池
   python helpers/recvin_link.py find --bill-no SPD00008770      # 按收票单号
   ```
   `resolve` 命中退出码 0、未命中 2，输出会给出建议用的那张 SPD 单号。
   查不到 → **直接服务端上传建票**（员工零操作）：
   ```bash
   python helpers/recvin_link.py create-recvin <发票文件> --bill-fid <FID> \
          --org 101 --item-name "*交通运输服务*客运服务费" --link <FID>
   ```
   ⛔ **不要"手工建票兜底"**：手工建的票永远没有流水号，会被提交闸门拦（见 §7）。
4. **把发票挂进收票信息**（纯 WebAPI，不需要发票云客户端）：
   ```bash
   python helpers/recvin_link.py link <报销单FID> <收票单号>            # 追加
   python helpers/recvin_link.py link <报销单FID> <收票单号> --replace  # 覆盖
   python helpers/recvin_link.py clear <报销单FID>                     # 清空
   python helpers/recvin_link.py set-contact <报销单FID> <员工号>       # 补往来单位（必填项）
   python helpers/recvin_link.py precheck <报销单FID>                  # 提交前体检
   python helpers/recvin_link.py submit <报销单FID>                    # 提交（内置闸门）
   python helpers/recvin_link.py verify <报销单FID> <收票单号>          # 一键自检
   ```
   写法要点：`Model = {"FID":.., "FRecInvInfo":[{"FRecInv":{"FBillNo":"<收票单号>"}}]}`
   —— 字段**必须放在 `FRecInvInfo[]` 数组行内**，放单据头会报
   `实体不存在此属性！[EntityType：BillHead]`。详见 cookbook §4.2。
   - ⚠️ **防抢关联**：一张收票单只能属于一张报销单，挂第二张会**静默抢走**原单关联。
     写之前先看收票单的 `FLINKBILLID`；helper 已内置拦截（`--allow-steal` 才放行）。
   - ⚠️ 已审核(C)单据同样可写 —— 操作前先看单据状态，别误改历史单。
5. **让明细与发票一致**：明细金额 = 实际报销额，且**必须 ≤ 发票价税合计**。
   金蝶 18 个操作里**没有**「合并生成费用明细」这个可调 API，它只是 UI 联动动作，
   底层落到两处：① 明细行 `FReimbLinkInvCode`/`FRecInvBillNo`/`FInvNumber`/`FInvOpenDate` 打标；
   ② 联动表 `FReimbAndRecInvInfo` 逐条发票分录。
   ```bash
   python helpers/expense_edit.py set-detail <FID> --entry <明细分录内码> --amount 8.00
   python helpers/expense_edit.py linkage <FID> --recv SPD00000002 --entry <明细分录内码>
   ```
   `set-contact` 要先跑：`ValidateFlag=true` 时 `FCONTACTUNIT` 是**必填**（否则 `MsgCode=11`）。
6. **附件分流**：行程单/说明走附件，**不要**传收票信息（会双算）。
   ```bash
   python helpers/expense_edit.py attach <FID> "D:/x/行程单.pdf"
   ```
   ⚠️ **`IsLast` 必须显式传 `true`** —— 漏传会被反序列化成 `false`，接口照样返回 `IsSuccess=true`
   和一个 `FileId`，但**内容根本没进文件信息表**（回环下载报"数据库文件信息表中不存在编码为 X 的文件信息"）。
   helper 已内置「上传后立刻回环下载比字节」的校验，别只看 IsSuccess。
7. **提交审批**：`precheck <FID>` → `submit <FID>`（有阻断项自动中止）。
   已实测 A(暂存) → B(已提交)，提交后收票信息保持。
8. 付款阶段由财务在金蝶操作，本 skill 只负责到"提交审批"。
9. **收尾自检**：`recvin_link.py verify <FID> <收票单号>`（校验两侧行数与回写），
   再跑 `expense_edit.py check <FID>` 看金额链条（报销 ≤ 发票、联动合计 = 发票合计）。

## 5. 🔒 提交硬闸门（无任何绕过参数）

两组判据，**全部硬拦**，命中任意一条即在发出 `Submit` **之前**中止。

**第一组 · 挂票前置（`guard_recv_invoices`，`link` 阶段）**

| # | 判据 | 后果 |
|---|---|---|
| 1 | 收票单在系统里查不到 | 中止，不写入挂票关系 |
| 2 | 收票单来源组织 ≠ 报销单组织（跨组织借票） | 中止 |
| 3 | 收票单没有发票云流水号（`FPIAOZONESERIALNUMBER` 为空） | 中止 |

> 判据 3 是用户明确要求："**不能提交上传发票没有流水号的报销单**"。
> 流水号是"这张票真的进过发票云"的唯一凭证，也是**防重复报销的锚点** —— 没有它，
> 同一张票可以被反复挂到不同单上，系统不会识别。
> ⚠️ 手工建票（`FGENERATETYPE=' '`）**永远拿不到流水号**（挂单不补、事后重下也不补，已实测）。

**第二组 · 提交内置（`submit()` 内无条件执行）**

| # | 判据 | 说明 |
|---|---|---|
| 4 | 金蝶自己的严格校验（`strict_probe` → `ValidateFlag=true`） | 用金蝶规则判，口径不会跑偏 |
| 5 | skill 自算口径（`precheck`） | 补第 4 道的盲区；`发票合计 < 报销金额` → block；**收票 0 行 + 报销金额 > 0 → block** |

- **没有任何运行时开关**：`--force` / `--allow-no-invoice` / `--allow-cross-org` / `--allow-no-piaozone`
  **全部已删除**，函数签名里也没有 `allow_*` 参数。
- 命中 → `submit()` 返回 `(False, {"MsgCode": "GUARD", ...})`，CLI 退出码 `3`；
  `link()` 命中 → 退出码 `2`。
- **唯一例外通道是"改源码"**：`recvin_link.py` 顶部 `ATTACHMENT_ONLY_ORGS = ()` 默认空；
  只有**明确以附件（不挂收票信息）为既有做法**的组织才在此登记（历史上只有 org 105）。
  要放行必须改代码，等于必须走一次人工评审。

**判据 4 有盲区**：`strict_probe` 靠 `Save` 触发，会被"编辑锁冲突"打断（`MsgCode=4`），
此时 errors 里没有发票金额那句 → 该道**静默放行**。所以判据 5 是主力，判据 4 只是"金蝶视角的加分证据"。

**闸门边界（别过度承诺）**：只管"发票金额 / 发票来源"这类风险，**不管单据完整性**（必填项是否填全）；
闸门防的是"误用默认参数"和"图省事的越狱"，**不是恶意改代码**（调用方直接 `kd._svc("Submit", ...)` 仍可跳过）。

## 6. 金额口径

- 🆕 **报销金额可以大于申请金额**（业务上没问题）→ **不要硬拦截，提醒用户即可**。
  「关联费用申请金额超出源单已下推报销金额」这条**只在下推时校验、保存报销单时不校验**；
  但改完要回看源单反写值是否已超申请额，超了就告知用户。
- **发票合计 ≥ 申请金额** → 提示"可按申请金额 或 按发票金额"，**让用户选**；
  **发票合计 < 申请金额** → **按发票金额**（合规），并明确告知差额。`fit` 只支持调低。
- **`报销金额 ≤ 发票价税合计` 是本 skill 的体检标准，不是金蝶的强制校验** —— 别把它说成"金蝶会报错"。
- 🔴 **`Submit` 永远返回 `IsSuccess=true`**：WebAPI 不校验金额、不跑表单插件。
  **"提交成功" ≠ "校验通过"**，不能拿它当金额合规的证据；金额合规**只能由 skill 自己算**。
- ⚠️ **"系统不拦"≠"可以乱报"**：金额不匹配的单在实际财务流转中仍会被人工/制度退回，
  所以 `precheck` 的阻断是**有意为之的业务闸门**，且已内置到提交里。
- **正常情况**按实际发生额报销，明细跟发票走；申请单金额只是预估。
- **发票 > 申请单**：先与用户确认按哪个金额，并排查是否"多开"；若属多开，必须按**实际付款金额**报销。
- **发票 < 申请单**：属正常下调，按发票金额提交。
- **凡申请单 ↔ 发票不一致，一律先跟用户确认再提交**，不要自行拍板。
- **网约车**：必须同步提交**行程单**；识别到就传附件，未识别到要提醒财务可能驳回（不硬拦）。
  **行程单金额绝不能当发票金额** —— 本环境系统会把行程单也识别成发票金额造成双算。

### 报销金额 > 发票金额：按发票金额调低（唯一正路）

```bash
python helpers/expense_edit.py --travel fit 101746              # 默认 dry-run，只算不写
python helpers/expense_edit.py --travel fit 101746 --apply      # 确认后真写
python helpers/expense_edit.py --travel fit 101464 --mode ratio # 多行按比例（默认从末行冲减）
python helpers/expense_edit.py --travel fit 101746 --target 900 # 显式指定目标
python helpers/expense_edit.py --travel check 101746            # 写完复查
```

`fit` 会：算发票合计 → 按 `sequential`（从最后一行往前冲减，贴近 UI 的"按发票金额调整"）或 `ratio` 分摊 →
打印逐行「旧→新 + 税额联动 + 差旅费金额」对照表 →（`--apply` 时）逐行写。**税额按同比例缩放**（保持税率不变）。

**金额勾稽**（改金额时必须一起改，否则报「差旅费金额不等于税额加费用金额」）：

```
含税 FExpenseAmount = 不含税 FTaxSubmitAmt + 税额 FTaxAmt
                    = FLOCNOTAXAMOUNT = FTravelAmount（差旅单专有）
```

⚠️ 差旅费金额的键是 **`FTravelAmount`**，不是 `FExpTravelAmount`；
`FTaxSubmitAmt` 名字里带 Submit 但它是**不含税**金额。

**方向不对称**：**调低安全，调高受限** —— 在报销单上加钱会被
「关联费用申请金额超出源单已下推报销金额！」拦住；想多报只能回出差申请单加额度。
所以 `fit` 对"目标 > 发票合计"一律硬拒。
旧版的 `--force`（关校验）已删除：它只让单据"写进去"，审核环节仍会被退。

### 🔴🔴 `Save` 成功会把「暂存(Z)」推进成「已提交(A)」

`set_detail` / `fit` 虽然只改金额，但写入成功会把单据推进状态，而 `A` 之后 **WebAPI 撤不回**
（`UnSubmit` 不存在、`UnAudit` 报"已关联工作流实例"）。两条纪律：

1. **绝不要拿别人的草稿单做写入测试。** 要验证写入就自建测试单（`Save` 新建 → Z），测完 `Delete`
   （只放 Z/A/D）。⚠️ 一旦 Submit 进流程，**测试单基本收不回来**。
2. 对真实单跑 `fit --apply` 前，确认该单本来就该进提交态，或先让用户点头。

> 只想算不写就用默认 dry-run（不带 `--apply`）—— 纯读，不触发任何状态变化。

## 7. 📥 发票从哪来

**流水号只在「发票云采集这张票的那一刻」产生**（实测：手机小程序上传、往收票单传附件、
手工建票后补下载，**都不会补号**）。所以：

- ⛔ 每日"下载昨日发票"**无法自动化** —— 每次都要电子税务局账号的手机验证码，不要再设计定时归集。
- ✅ **定稿路线（v2.1.0 起）：agent 服务端上传 → 真流水号 → 建收票单 → 挂单**，员工零操作：

```bash
python helpers/recvin_link.py upload <发票文件> --bill-fid <报销单FID>   # 只上传拿流水号，不写金蝶
python helpers/recvin_link.py create-recvin <发票文件> --bill-fid <FID> \
       --org 101 --item-name "*交通运输服务*客运服务费" [--link <FID>]
```

上传接口自带识别与查验（`POST {发票云}/m4/fpzs/expense/upload?userKey=…`，multipart 字段固定 `file`，
成功返回 `0000 查验成功`），返回发票号/金额/税额/购销方/**33 位流水号** → **不需要 OCR 模型**；
员工在界面点「发票导入单据」生成的收票单，其 `FPIAOZONESERIALNUMBER` 与服务端上传拿到的**逐字符一致**。

> 🔴 **红线**：只有 `upload` 返回的**真实验票流水号**才允许手填；凭空填一个编号仍是闸门要拦的假票。
> ⚠️ 发票云不返回明细项目名称，`--item-name` 由 agent 读发票图提供。
> ⚠️ `--bill-fid` 必填（发票云要求 `bxd_key` 非空），所以**先有报销单才能上传**。
> "只上传不建票"不够：金蝶收票池不会因此生成收票单，必须 `create-recvin`。

**为什么必须"有流水号"**：`FPIAOZONESERIALNUMBER` 是发票云**防重复报销**的锚点
（按 `expenseStatus` 未用/在用/已用锁票）。没有流水号 = 发票云不知道这张票被报销了 → 同一张票可被报两次，
而金蝶本身**不校验发票号重复**。

**路线选择**：**推荐路线1（中央 agent + 服务账号，员工零操作、前端看不到池子）**；
❌ 不把服务账号下发到员工电脑（等于全公司拿到管理员账号）；
❌ 不把"只能查看自己单据"整个关掉（实测 = 整组织可见，同事私有票全露）。
详见 `references/implementation-notes.md` §5。

## 8. 🔴 跨组织报销：禁止

**把 A 组织的发票挂到 B 组织的报销单上 → 不能。而且是"能写进去、单据却不可用"的那种不能。**

> 🔴 `发票购方名称 ≠ 报销单组织名称` ＝ **不许挂**。这是**税务归属**问题（谁的进项、谁抵扣），
> 系统在界面/审核环节也会挡。这是**阻断项，不是"提醒一下再确认"**。

先分清三个独立关卡，别把"建票成功"当成"单据可用"：

- **建收票单** = `Save IV_ReceivedInvoice`，往收票池加一张票 —— 与报销单无关。
- **挂票** = `Save FRecInvInfo`，把票关联到某张报销单的收票信息。
- **单据可用** = 界面/审核时按**本组织税号**能从发票云取到这张票的流水号。

实测表现：`Save` ✅ → `Submit` ✅（读到 B）→ 随后变 **`D`**，界面点「查看发票」报
「无法获取当前关联收票单的发票云发票流水号，请尝试删除收票单后重做收票」。
`D` = 审核驳回，但 `APPROVERID` 为空 —— **根本没有审批人参与**，是取不到票导致的操作被拒。

**三条硬约束（`guard_recv_invoices()` 写入前拦截）**

| # | 约束 | 违反后 |
|---|---|---|
| 1 | 收票单 `SOURCEORGID`（来源组织）**必须 = 报销单组织** | 界面报取不到流水号 → `D` |
| 2 | 收票单必须由发票云归集（`FPDFURL` / `FPIAOZONESERIALNUMBER` 非空） | 同上（同组织也会报） |
| 3 | 目标组织的**收票服务许可**须在有效期内 | 发票云报「【收票服务】许可已过期失效 [0300]」 |

**拦截落在两道口子上**：
```
recvin_link.py link   <FID> <收票单号…>  → guard_recv_invoices()，Save 之前就拦，不写库
recvin_link.py submit <FID>              → cmd_submit 先跑 precheck，有阻断项即 return，连 Submit 都不调
```

⚠️ **挂错要还原**：挂票会把收票单的 `SETTLEORGID`（结算组织）改成目标报销单组织，并回写
`LINKBILLID/LINKIVNUMBER/LINKBILLTYPE` → 撤销时要把 `SETTLEORGID` 改回原组织并清掉 LINK 三件套，
否则这张票在原组织的收票核算里也是错的。

⚠️ **「收票信息为空」不是阻断项**（已降级为 warn）：org 105 的历史单 0 行照样审核到 C。
org 105 这类**只用附件**的组织，跟随其既有做法走附件即可 —— 技术层面「能挂」≠ 业务层面「该挂」。

## 9. 已知坑速查

| 现象 / 要点 | 处理 |
|---|---|
| `Save` 字段必须带 `F` 前缀 | View 返回 `ExpenseAmount`，Save 要写 `FExpenseAmount`，否则**静默忽略** |
| `FCONTACTUNIT`（往来单位）为空 | 下推后为空，Submit 前必须局部更新。Save 键：`FCONTACTUNIT` + `FCONTACTUNITTYPE`，类型 `BD_Empinfo`，引用 `{"FNumber":"员工号"}` 或 `{"Id":内码}`；值 = **申请人本人**（`ProposerID`） |
| 元数据标着 `IsNewLock/IsEditLock=True` | **不代表写不进**（`FRecInv` 实测可写）。别拿锁标记当结论，要实测 |
| 报 `[EntityType：BillHead]` | 字段层级放错了，`FRecInv`/`FIVSerialNo` 必须在 `FRecInvInfo[]` 数组行内 |
| `FIVSerialNo` 要不要写 | 不必。官方产物里它是空的，承载关联的是 `FRecInv`；真填是 `FPDFURL` 尾部 **32 位** hash，不是 33 位流水号 |
| 收票单 `Save` 的 Model 结构 | **扁平**：表头字段直接写 Model 根（`FIVNUMBER` / `FOPENDATE` / `FSUMAMOUNT` / `FSUMTAXAMOUNT` / `FSUMALLAMOUNT` / `FPURNAME` / `FSALENAME`…），**不要**用 `FBillHead` 包裹 —— 包了会 `IsSuccess=true` 但表头字段**全部静默丢弃**（只剩分录行） |
| 更新收票单分录出现 2 行变 4 行 | 不带 `FEntryID` 直接再 Save 会**追加**分录。加 `IsDeleteEntry=true` 清旧重建；该开关只清分录不动表头 |
| 新增分录行被静默丢弃 | `NeedUpDateFields` 与"新增分录行"**互斥**：改字段一次 Save（带），新增分录另起一次（不带） |
| 联动表 `FReimbAndRecInvInfo` 键名 | 每条分录：`FRecInvFid` / `FRecInvEntryId` / `FInvAllAmt`；明细侧打标 `FReimbLinkInvCode` / `FRecInvBillNo` / `FInvNumber` / `FInvOpenDate` |
| 差旅单必填与勾稽键 | `FTravelType`（+`_Id`，差旅费类型，必录）、`FTravelAmount`（= `FTaxSubmitAmt` + `FTaxAmt` = 含税）。改金额要三个一起改 |
| 报销单的组织字段 | 是 `FOrgID` / `FExpenseOrgId` / `FPayOrgId`；`FCostOrgID` 是**费用申请单**才有的，给报销单查它会报"字段不存在" |
| 单据状态口径 | 字段 `FDocumentStatus`：`Z` 暂存 / `A` 创建（已提交）/ `B` 审核中 / `C` 已审核 / `D` 重新审核（驳回）。另有关闭 `FCloseStatus`、作废 `FCanCancel` 是**独立维度** |
| 附件上传端点 | `DynamicFormService.UploadFile.common.kdsvc`（只拿 `FileId`，需 `FileName`+`SendByte`+`IsLast`）/ `AttachmentUpLoad.common.kdsvc`（一步挂单，需 `FileName`/`FormId`/`IsLast`/`InterId`/`BillNO`/`SendByte`）/ `AttachmentDownLoad.common.kdsvc`（回环，传 `FileID`，回 `FilePart`+`FileSize`+`FileName`） |
| 不存在的附件接口 | `FileUploadService`、`GetAttachmentList`、`DownloadFile`、`GetFileInfo`、`Attachment.AttachmentService` 命名空间**都没有**，别猜 |
| 猜接口契约 | 发空包读报错最快（报错会说"参数X必传"）。`BOS_Attachment` 用 `ExecuteBillQuery` 会报「数据库执行异常」，验证附件一律走 `AttachmentDownLoad` 回环比字节 |
| `ExecuteBillQuery` 字段名 | 与 View 不同：View `PDFURL`/`PURNAME` → 查询写 `FPDFURL`/`FPURNAME`；**返回列顺序 = FieldKeys 顺序** |
| 查询返回 `[[{"Result":…}]]` | **错误伪装成数据行**，接口不抛异常。取数前先剥嵌套 list 再判 |
| 标准库直连必须带 CookieJar | 否则报"会话信息已丢失"，且 `View` 返回空字典**不报错**（极易误判"没数据"） |
| 编辑锁冲突 | 瞬时问题，重试即可（helper 已内置）。旧笔记"需客户端关闭或重下推"是过度结论 |
| 收票池查不到某张票 | ① 先用**有权限账号**重查（账号可见性会造成假阴性）；② 票没被采集过；③ 号码错；④ 属其它组织 |
| 行程单被采集成了收票单 | 靠字段识别，不能只看金额：`INVOICETYPE=11`、发票号空或单个空格、`FSALENAME`/`FPURNAME` 空、`FISELECTRONIC=False`、`ISEXAMINE=3`。挂票前必须核验「发票号码 + 销方」非空 |
| `INVOICETYPE` 票种 | `26` 数电普票 / `27` 数电专票 / `29` 铁路电子客票 / `11` 未识别（多为行程单） |
| 同一张票既挂收票信息又传附件 | 重复佐证，应二选一。默认能挂就只挂收票信息 |
| 收票单 `FLINKIVNUMBER` 有值 | 可能是**悬空号**（原单已删）。先按单号反查该单是否真存在，再判断"已被用掉" |
| 判定"某字段能不能写" | `QueryBusinessInfo` 返回 `Entrys[]` / `Operations[]`，能看结构和实体名，但可写性不能只看锁标记，要实测。View 用 `EntryName`（`RecInvInfo`），Save 用 `Key`（`FRecInvInfo`） |
| 联动表 0 行 | **不是**提交阻断项（真实已审核单 100003/100004 就是 0 行） |

> 上述每条都有实测过程与证据，见 `references/implementation-notes.md`。

## 10. 脚本索引

| 脚本 | 作用 |
|---|---|
| `helpers/setup_credentials.py` ★ | 配置向导：弹窗填公司名/用户名/密码；`--check` 看状态、`--delete` 清除、`--change-key` 改密钥、`--console` 强制文字向导。**缺 tkinter 自动换解释器 → 再转文字向导（零安装）**；退出码 0/10/12/2/3 需按码判读。内置明文存量迁移 |
| `helpers/credstore.py` ★ | DPAPI 加密凭据存储（纯标准库 `ctypes`）；`describe()` 只回报字段名，永不回显值 |
| `helpers/recvin_link.py` ★ | 核心：收票信息写入与提交。`find` / `list` / `resolve` / `link` / `clear` / `precheck` / `set-contact` / `submit` / `verify` / `upload` / `create-recvin` / `strict`；内置防抢关联、编辑锁重试、提交闸门 |
| `helpers/expense_edit.py` ★ | 明细 ↔ 发票对齐与附件。`attach` / `set-detail` / `linkage` / `fit` / `download` / `check` |
| `helpers/perm_probe.py` | **只读**权限可见性实测器：`--pool` / `--by-invoice` / `--by-bill` / `--bills` / `--fields` / `--json` |
| `helpers/invoice_classifier.py` | 发票 vs 行程单分类、字段提取、双算预警 |
| `helpers/piazzone_tool.py` | 发票云 API 兜底（仅"发票不在池里需程序化采集"时有意义），已内置域名 failover |

下推：`Push`，`formid` 填源单、`TargetFormId` 填目标。新建收票单：`Save IV_ReceivedInvoice`（扁平 Model）。

## 11. 深入阅读

| 文件 | 内容 |
|---|---|
| `references/field_cookbook.md` | 费用申请单 / 出差申请单的 Save 模板与全部必填字段；查询与载荷写法 |
| `references/policy_rules.md` | 报销制度**模板**（全是【待填】空位） |
| `references/implementation-notes.md` | **实测记录与版本沿革**：版本变更、`ValidateFlag` 越狱机制、闸门验证记录、金额勾稽原始数据、发票云与手工建票对比、跨组织三轮修正、收票池可见性三组对照、权限三层次、编辑锁与网络抖动 |
| `official-api/` | 金蝶 WebAPI 官方原始文档（`WebAPI-Save.txt` / `WebAPI-Submit.txt` / `IV_ReceivedInvoice.txt`）；目录与文件名用 ASCII，避免压缩包跨系统解压乱码 |

## 报销制度提醒（制度不内置）

- ⚠️ 各公司制度不同，本 skill **不内置任何限额 / 审批要求**。模板见 `references/policy_rules.md`，全是【待填】。
- 使用方把本公司规则写到 `references/policy_rules.local.md`（已列入 `.gitignore`/`.clawhubignore`），
  agent **优先读它**，读不到就只按通用口径处理。
- 🔴 模板里写【待填】的项，agent 必须**明确说"贵司这项还没登记"**，
  **绝不许拿别家数值或"常见做法"当默认** —— 编一个限额出来，比不提醒更糟。
- 与实现绑定的通用部分只有两块：① 金额与发票一致性口径（`fit` 调低；只能回源单加额度）；
  ② 行程单 vs 发票的双算预警（行程单一律传附件）。
