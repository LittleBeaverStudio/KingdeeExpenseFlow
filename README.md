> **本项目采用「小河狸工作室非转售许可 1.0」**——
> 个人使用与**企业内部使用免费**（含内部办公、财务与会计处理、内部系统集成）；
> 对外销售、集成进收费产品或服务、托管 / SaaS / 代运营、以及**向第三方交付成果或提供服务，须事先取得书面授权**。
> 许可全文见 [LICENSE](./LICENSE)。商业授权：https://littlebeaver.top

[![License](https://img.shields.io/badge/License-%E5%B0%8F%E6%B2%B3%E7%8B%B8%E9%9D%9E%E8%BD%AC%E5%94%AE%E8%AE%B8%E5%8F%AF-blue.svg)](./LICENSE)

# 金蝶云星空报销助手 Skill

让员工**不登录金蝶云星空**，直接在对话框里把发票交上来，由 AI 完成「费用申请单 → 报销单 → 挂发票 → 提交审批」的全过程。适合需要报销但不想学金蝶操作的业务同事，以及想把报销流程接进自己系统的开发者。

## ✨ 能做什么

- 建费用申请单 / 出差申请单，并提交、下推报销单
- 把发票挂进报销单的「**收票信息**」（纯金蝶 WebAPI，不需要发票云授权）
- 提交**硬闸门**：收票 0 行 / 缺发票云流水号 / 跨组织挂票 / 发票合计 < 报销金额 —— 命中即中止，**无绕过参数**
- 发票不在收票池时，指引用**员工在报销软件里上传一次**拿到发票云流水号（手工建票已证伪，见下）
- 让报销明细与发票一致（金额、费用项目对齐，写入「明细 ↔ 发票」联动关系）
- 把**行程单**传成**附件**，避免与发票重复计入金额
- 提交前体检：金额链条、收票信息、往来单位一次查清
- 提交审批（提交后收票信息保持）

覆盖 `ER_ExpenseRequest` / `ER_ExpReimbursement`（费用线）与 `ER_ExpenseRequest_Travel` / `ER_ExpReimbursement_Travel`（差旅线）。

## 🚀 三步开始

### 1. 检查运行环境

核心链路**只用 Python 标准库**，不需要装任何第三方包：

```bash
python --version   # 需要 3.8 或更高
```

### 2. 连接配置：推荐用配置向导（明文不落盘）

本 Skill 与本工作室的其它金蝶技能**共用同一份连接配置**。推荐用向导填一次：

```bash
python helpers/setup_credentials.py
```

弹窗里**只需填 3 项：公司名（账套名称）、用户名、密码**。凭据用 **Windows 系统加密（DPAPI）**
保存到 `~/.workbuddy/kingdee/credentials.bin`：

- 🔒 绑当前 Windows 用户 —— 同机其它用户读不出，拷到别的机器也解不开
- 🔒 不写明文文件、不进对话记录、不落日志（向导只回报字段名是否存在，永不回显值）
- 🔒 在技能目录之外，升级 / 重装 / 打包都不会带上它
- 🧩 **不要求你装任何东西**：本机 Python 没带图形界面时，会自动找本机已装的另一个 Python；
  找不到就用**自带的原生 Windows 表单**（有输入框、密码框、显示密码）；
  万一连表单都起不来，才转文字向导（独立窗口录入，密码同样不进对话记录）。**全程不做静默安装。**
- ↩️ 窗口关掉 = 没有保存：向导会用退出码明确区分「已保存」和「你取消了」，不会把取消当成功。

**两类凭据分两个文件、各有唯一写入者**（互不覆盖）：

| 凭据 | 文件 | 谁写 |
|---|---|---|
| 金蝶连接（服务器/账套/用户名/密码） | `~/.workbuddy/kingdee/credentials.bin` | **只有导出技能**（推荐用它配） |
| 发票云（client_id/secret/税号） | `~/.workbuddy/kingdee/piazzone.bin` | **只有本技能** |

```bash
python helpers/setup_credentials.py --piazzone   # 只维护发票云（金蝶连接已配好时）
python helpers/setup_credentials.py --full       # 连金蝶连接一起录（只装了本技能时才用）
```

改金蝶密码请用导出技能的 `python setup_credentials.py --change-key` —— 一份凭据只有一个入口，
改完两边都生效。早期版本把发票云塞在 `credentials.bin` 里，导出技能改密码会把它冲掉；
现在向导启动时会自动迁移到 `piazzone.bin`（旧文件先备份成 `.bak-<时间戳>`）。

只看状态、不开窗：`python helpers/setup_credentials.py --check`

如果已经装好并配置过
[`kingdee-data-exporter`](https://github.com/LittleBeaverStudio/KingdeeDataExporter)
（金蝶云星空数据导出），本 Skill 会**自动复用它的连接配置**，不需要重复填写。

**只有这几种情况才需要别的配法**（按推荐顺序）：

| 情况 | 做法 | 边界 |
|---|---|---|
| 普通用户（推荐） | 金蝶连接用导出技能的向导；发票云用 `helpers/setup_credentials.py --piazzone` | 明文不落盘，绑当前 Windows 用户。缺 tkinter 会自动换解释器 → 原生表单 → 文字向导 |
| 缺 tkinter 的机器 | 同上，自动改用内置原生表单 | 标准 Windows 控件，不装任何东西 |
| 完全没有图形界面 | 同上，向导自动转成文字向导（独立黑窗口） | 密码输在那个窗口里，不进对话记录 |
| CI / 无人值守 | 环境变量 `KINGDEE_BASE_URL` / `KINGDEE_ACCTID`（或 `KINGDEE_ACCT_NAME`）/ `KINGDEE_USERNAME` / `KINGDEE_PASSWORD` | ⚠️ 环境变量不会被误提交进 git，但**本地并不保密**：同用户其它进程可读、会进日志与崩溃转储 |
| 旧版明文写法（兼容保留，不推荐） | 写 `~/.workbuddy/kingdee/config.json` | 读到含密码时会提示迁移 |
| 导出技能的 `config.py` 旧写法 | **无需操作**，同样兼容 | —— |

**读取优先级：环境变量 > 系统加密凭据 > 明文 `config.json` > 导出技能 `config.py`。**

**从旧版明文迁移**（装过旧版的用户请做一次）：本机若存在 `~/.workbuddy/kingdee/config.json`，
跑一次向导即可 —— 它会读入旧值、加密保存、**默认勾选删除该明文文件**，并提示
**到金蝶后台修改一次密码**（旧密码曾以明文存在本机，改掉才算真正安全）。

旧版明文写法（仅作参考，`~/.workbuddy/kingdee/config.json`）：

```json
{
  "base_url": "https://你的域名/k3cloud/",
  "acct_name": "账套名称",
  "username": "集成账号",
  "password": "密码"
}
```

> 账套 ID 不用自己找 —— 只填账套名称会自动解析；想列出来看看就跑一次
> （免账号密码）：`python data_exporter.py --list-datacenters`

> 🔐 无论哪种方式，都不要把真实账号、密码或账套 ID 提交到公开仓库。
> 建议给这个 Skill **单独建一个集成用户**，只授予报销相关单据与收票单的权限，不要用管理员账号。

> 📌 **发票云（piazzone）是可选层**：客户只需维护 公司名 / 用户名 / 密码。只有需要
> 「agent 服务端上传发票」这类功能时才额外配置，且必须用自己企业的发票云授权；
> 主链路（收票信息写入、明细对齐、附件、提交）**纯金蝶 WebAPI，不需要发票云授权**。
>
> 🔑 **多组织注意**：发票云权益**按税号划分**，同一账套下**每家买了收票服务的公司各有一组**
> `client_id / 购方税号`（官方：「请使用税号对应的企业授权，不要用租户授权！！」）。
> 向导里**一家一框**、只填 4 项，多家就「＋ 添加一家」——
> **不用填组织名、也不用填「对应金蝶组织」**：组织名自动取购方企业全称，
> 上传时按**报销单的组织名称**自动选中对应那家（`--org` 可以留空）。
> 没对上会**拒绝上传**（`ORGUNMATCHED`），不会拿别家的授权硬跑。

> 为什么本 Skill 自己也要读一次凭据？因为它要做**写操作**（提交单据、下推、挂传附件），
> 而导出技能是**只读**的、不提供写接口。连接动作必须自己完成，但读的是**同一份配置**，
> 用户不需要多填任何东西。

### 3. 先做一次只读体检

任何写操作之前，先体检一下，确认能连通、单据状态符合预期：

```bash
python helpers/recvin_link.py precheck <报销单FID>
python helpers/expense_edit.py check <报销单FID>
```

## 🧭 推荐使用顺序

### 第一步：下推报销单

```bash
# 费用申请单 → 费用报销单
# formid=ER_ExpenseRequest, TargetFormId=ER_ExpReimbursement
```

也可以手工新建报销单，两种来源都能走后面的流程。

### 第二步：先「找」发票，再「建」收票单

```bash
python helpers/recvin_link.py find 24000000000000000001        # 按发票号码找
python helpers/recvin_link.py find SPD00000001                 # 按收票单号找
```

查不到 → **直接用 agent 服务端上传建票**（v2.1.0 起，**员工零操作**）：

```bash
# 上传发票（走真实验票通道）→ 拿 33 位流水号 → 建收票单 →（可选）挂到报销单
python helpers/recvin_link.py create-recvin 发票.jpg --bill-fid <报销单FID> \
       --org 101 --item-name "*交通运输服务*客运服务费" --link <报销单FID>

# 只想先看看能不能识别 / 拿流水号，不写金蝶任何数据：
python helpers/recvin_link.py upload 发票.jpg --bill-fid <报销单FID>
```

⚠️ 它等价于金蝶界面上的「选择发票 → 上传 → 发票导入单据」，但无需人工点击。
🔴 **红线**：手填的流水号必须是 `upload` 返回的**真实验票流水号**；
凭空手填编号仍然是硬闸门要拦的假流水号票。
（发票云**不返回**发票明细项目名称，所以 `--item-name` 要由 agent 读发票图提供。）

> ⚠️ **一张收票单只能属于一张报销单。** 把它挂到第二张单上会**静默改掉**原单的关联，
> 造成「旧单还列着这张票、票却说属于别人」的不一致。工具默认拦截，确需改挂要显式加 `--allow-steal`。

### 第三步：把发票挂进「收票信息」

```bash
python helpers/recvin_link.py link <报销单FID> <收票单号>             # 追加
python helpers/recvin_link.py link <报销单FID> <收票单号> --replace   # 覆盖
python helpers/recvin_link.py clear <报销单FID>                       # 清空
```

差旅费报销单加 `--travel`：

```bash
python helpers/recvin_link.py --travel link <报销单FID> <收票单号>
```

### 第四步：让明细与发票一致

体检一下，再按发票金额改明细：

```bash
python helpers/expense_edit.py set-detail <报销单FID> --entry <明细分录内码> --amount 88.00
python helpers/expense_edit.py linkage <报销单FID> --recv SPD00000001 --entry <明细分录内码>
```

`linkage` 写的是「明细 ↔ 发票分录」的联动关系 —— 也就是金蝶界面上选票时那个**「合并生成费用明细」**的底层数据。

### 第五步：行程单进附件

```bash
python helpers/expense_edit.py attach <报销单FID> "D:/票据/行程单.pdf"
```

### 第六步：补往来单位 → 体检 → 提交

```bash
python helpers/recvin_link.py set-contact <报销单FID> <员工号>
python helpers/recvin_link.py precheck <报销单FID>
python helpers/recvin_link.py submit <报销单FID>
```

`submit` 会先跑硬闸门：**收票 0 行、缺发票云流水号、跨组织挂票、发票合计 < 报销金额** 命中任意一条
都会**直接中止、不发起提交**，而且 **v2.0.11 起没有任何 `--force` 之类的绕过参数**。
有阻断项时正确动作是**修数据**：`expense_edit.py fit` 把报销额调到发票合计，或让员工把缺的票补上传。

## 🧾 金额与费用类型口径

- **硬规则：报销金额 ≤ 发票价税合计**。否则金蝶会报「发票金额不允许小于报销金额」。
- **明细跟发票走**。费用申请单的金额只是预估，实际发生可以在报销时调整：
  申请 100 元（招待 80 + 交通 20）→ 实际发票 90 元（招待 85 + 交通 5），**明细就按发票写 85 / 5**，
  必要时连**费用项目**一起改。
- **发票 > 申请单**：先跟申请人确认按哪个金额提交，并排查**是否多开**。
  若属多开，**不能按发票金额**，必须按**实际付款金额**报销。
- **发票 < 申请单**：属正常下调，按发票金额提交。
- **凡申请单 ↔ 发票不一致，一律先跟申请人确认再提交**，不要自行拍板。

## 🎒 发票和行程单要分开放（关键）

| 放哪 | 用什么 | 参与金额校验？ |
|---|---|---|
| **收票信息** | 报销单 `RecInvInfo` 单据体 → 关联收票单 | ✅ 是，「发票金额 ≥ 报销金额」读这里 |
| **附件** | `AttachmentUpLoad` | ❌ 否 |

- **网约车**必须同步提交**行程单**。识别到就传**附件**；没识别到要**提醒申请人财务可能驳回**（但不硬拦）。
- ⚠️ **行程单金额绝不能当发票金额**。部分环境会把行程单也 OCR 成发票金额，造成同一笔业务**双算**。
- 校验报错「发票金额不允许小于报销金额」= 收票信息里没有有效收票单（发票传错了位置，或被传到了附件）。

## 🛠️ 命令速查

```bash
# 收票信息 / 提交（helpers/recvin_link.py）
python helpers/recvin_link.py find <发票号码|收票单号>
python helpers/recvin_link.py list <报销单FID>
python helpers/recvin_link.py link <报销单FID> <收票单号> [--replace] [--allow-steal]
python helpers/recvin_link.py clear <报销单FID>
python helpers/recvin_link.py set-contact <报销单FID> <员工号>
python helpers/recvin_link.py precheck <报销单FID>
python helpers/recvin_link.py submit <报销单FID>
python helpers/recvin_link.py verify <报销单FID> <收票单号>

# 明细 / 联动 / 附件（helpers/expense_edit.py）
python helpers/expense_edit.py set-detail <报销单FID> --entry <分录内码> --amount <金额>
python helpers/expense_edit.py linkage <报销单FID> --recv <收票单号> --entry <分录内码>
python helpers/expense_edit.py attach <报销单FID> <文件路径>
python helpers/expense_edit.py download <FileId> [--out 本地路径]
python helpers/expense_edit.py check <报销单FID>

# 差旅费报销单：以上命令都加 --travel（写在子命令前或后均可，如 `--travel attach ...`）
```

## 🤖 让 AI 工具识别这个 Skill

仓库采用“一个仓库一个 Skill”的通用结构，`SKILL.md` 位于仓库根目录：

```text
KingdeeExpenseFlow/
├── SKILL.md                     AI 执行说明与触发描述
├── README.md
├── LICENSE
├── config.example.py            安全配置示例
├── requirements.txt
├── agents/openai.yaml           AI 客户端展示信息
├── helpers/
│   ├── recvin_link.py           收票信息写入 / 提交（核心）
│   ├── expense_edit.py          明细对齐 / 联动 / 附件（核心）
│   ├── invoice_classifier.py    发票 vs 行程单分类与双算预警
│   └── piazzone_*.py            发票云兜底（可选，默认不需要）
├── references/
│   ├── field_cookbook.md        字段级接口配方（实测）
│   └── policy_rules.md          报销制度**模板**（全是【待填】空位；本公司规则请放 `references/policy_rules.local.md`，该文件不进仓库）
└── official-api/                金蝶 WebAPI 原始文档（ASCII 命名，防解压乱码）
```

支持从 GitHub 扫描 Skill 的工具，可以直接使用仓库地址：

```text
https://github.com/LittleBeaverStudio/KingdeeExpenseFlow
```

识别后可这样发起任务：

```text
请使用 kingdee-expense-flow，帮我把这张发票提交成费用报销单，员工不登录金蝶。
```

### SkillHub / 其他管理器

能递归扫描 `SKILL.md` 的 SkillHub 或桌面管理器可以直接识别本仓库。若平台要求上传压缩包，请压缩整个仓库，并确认解压后根目录中仍有 `SKILL.md`。

## 📋 报销制度：本 Skill 只管机制，制度请自己填

各公司制度不一样（住宿限额、招待费审批、报销时限、税号……），所以**本 Skill 不内置任何具体制度**，
`references/policy_rules.md` 是一份**全是【待填】空位的模板**。

要接入贵司制度：

```bash
cp references/policy_rules.md references/policy_rules.local.md   # 然后按贵司规则填空
```

- `policy_rules.local.md` 已在 `.gitignore` / `.clawhubignore` 里，**不会进仓库、不会外泄**。
- agent 会**优先读**它；读不到就只按通用口径处理，并且对【待填】的项**明确说"还没登记"，不会替你编一个限额**。
- **只有两块是通用的**（与实现绑定，任何公司都适用）：① 金额与发票一致性口径（按发票金额调低报销额）；
  ② 行程单必须传附件（防止与发票重复计入）。

## 🛠️ 常见问题

### 提示「发票金额不允许小于报销金额」

报销金额比收票信息里的发票金额大。两种情况：

1. 发票没挂对位置 —— 用 `recvin_link.py list <FID>` 看收票信息是不是空的；
2. 报销金额该下调 —— 按实际发票金额改明细：`expense_edit.py set-detail … --amount <实际额>`。

### `find` 查不到发票

- 发票还没进收票池 —— ⚠️ **不要指望"系统会自动归集"**：金蝶后台的"下载发票"每次都要**电子税务局账号的手机验证码**，无法定时无人值守。**可靠做法是让员工在报销软件 / 发票云里把这张票上传一次**（当天进池、当天有流水号）；
- 号码填错；
- 该发票属于其它组织/账套；
- 该票已被删除（删单会释放归属）。

### 附件上传返回成功，但金蝶里看不到

`AttachmentUpLoad` 的 `IsLast` 是**必填布尔**，漏传会被当成「非最后分片」——接口照样返回 `IsSuccess=true` 和一个 FileId，但**内容根本没落库**。

判断办法只有一个：**回环下载比字节**（`expense_edit.py attach` 已内置该校验，会打印 md5 是否一致）。

### 报「单据编辑冲突，请稍候再使用」

这是编辑互斥锁的**瞬时**占用，等几秒重试即可（工具已内置自动重试）。不需要关客户端，也不需要重新下推单据。

### 提交后还能改吗

`B`（已提交）和 `C`（已审核）状态的单据都能继续 Save 改。**但操作已审核单据前务必确认状态**，别误改历史单。

## 📁 主要文件

```text
SKILL.md                     AI 执行说明与触发描述
README.md                    中文使用介绍
helpers/recvin_link.py       收票信息写入 / 往来单位 / 体检 / 提交（纯标准库）
helpers/expense_edit.py      明细金额对齐 / 明细↔发票联动 / 附件上传（纯标准库）
helpers/invoice_classifier.py 发票与行程单分类、字段提取、双算预警
references/field_cookbook.md  字段与接口配方手册（全部为实测结论）
references/policy_rules.md    差旅住宿/招待费等报销制度**模板**（全是【待填】空位；本公司规则请放 `references/policy_rules.local.md`，该文件不进仓库）
official-api/                  金蝶 WebAPI 官方文档（Save / Submit / 收票单）
```

> 本工具仅用于你有权访问的金蝶环境。使用者需要自行负责凭据保管、权限控制和数据合规。
> 报销金额、费用项目与发票的匹配口径，请以贵司财务制度为准。
