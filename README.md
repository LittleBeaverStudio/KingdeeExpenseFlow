> **许可变更**：自本次更新起，本项目许可由 MIT 变更为**「小河狸工作室非转售许可 1.0」**——
> 个人使用与**企业内部使用免费**（含内部办公、财务与会计处理、内部系统集成）；
> 对外销售、集成进收费产品或服务、托管 / SaaS / 代运营、以及**向第三方交付成果或提供服务，须事先取得书面授权**。
> 许可全文见 [LICENSE](./LICENSE)。商业授权：https://littlebeaver.top

[![License](https://img.shields.io/badge/License-%E5%B0%8F%E6%B2%B3%E7%8B%B8%E9%9D%9E%E8%BD%AC%E5%94%AE%E8%AE%B8%E5%8F%AF-blue.svg)](./LICENSE)

# 金蝶云星空报销助手 Skill

让员工**不登录金蝶云星空**，直接在对话框里把发票交上来，由 AI 完成「费用申请单 → 报销单 → 挂发票 → 提交审批」的全过程。适合需要报销但不想学金蝶操作的业务同事，以及想把报销流程接进自己系统的开发者。

## ✨ 能做什么

- 建费用申请单 / 出差申请单，并提交、下推报销单
- 把发票挂进报销单的「**收票信息**」（纯金蝶 WebAPI，不需要发票云授权）
- 发票不在收票池时，按发票原件**新建收票单**
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

### 2. 填写本地配置

复制 `config.example.py` 为 `config.py`，然后填写：

```python
KINGDEE_CONFIG = {
    "base_url": "https://你的金蝶地址/k3cloud/",
    "acctid": "账套ID",
    "username": "用户名",
    "password": "密码",
}
```

> 🔐 `config.py` 已加入 `.gitignore`。不要把真实账号、密码或账套 ID 提交到公开仓库。
> 也可以用环境变量 `KINGDEE_BASE_URL` / `KINGDEE_ACCTID` / `KINGDEE_USERNAME` / `KINGDEE_PASSWORD`，优先级高于文件。

建议给这个 Skill **单独建一个集成用户**，只授予报销相关单据与收票单的权限，不要用管理员账号。

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

查不到才新建收票单（`IV_ReceivedInvoice` 可以用标准 WebAPI 建）。

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

`submit` 会先体检，有阻断项会自动中止，不会盲提交。

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
│   └── policy_rules.md          报销制度标准
└── 官方接口说明/                金蝶 WebAPI 原始文档
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

## 🛠️ 常见问题

### 提示「发票金额不允许小于报销金额」

报销金额比收票信息里的发票金额大。两种情况：

1. 发票没挂对位置 —— 用 `recvin_link.py list <FID>` 看收票信息是不是空的；
2. 报销金额该下调 —— 按实际发票金额改明细：`expense_edit.py set-detail … --amount <实际额>`。

### `find` 查不到发票

- 发票还没进收票池（数电票通常开票次日凌晨自动归集，纸票需先采集）；
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
references/policy_rules.md    差旅住宿/招待费等报销制度标准
官方接口说明/                 金蝶 WebAPI 官方文档（Save / Submit / 收票单）
```

> 本工具仅用于你有权访问的金蝶环境。使用者需要自行负责凭据保管、权限控制和数据合规。
> 报销金额、费用项目与发票的匹配口径，请以贵司财务制度为准。
