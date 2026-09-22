# -*- coding: utf-8 -*-
"""数据权限可见性实测器 —— 用【指定身份】登录，回查"这个账号到底能看到什么"。

用途（这是本技能里最容易踩坑的一环）：
  · 排查「按发票号查不到收票单」时，判断是**真的没归集**，还是**这个账号/组织看不到**；
  · 上线前确认员工账号的可见范围（会不会看到同事的私有票）；
  · 验证「关掉/加上某条数据规则」之后，实际生效范围是不是你以为的那样。

⚠️ **只读**：全程只有 `ExecuteBillQuery` / `View`，不写任何数据。

用法：
  # 1) 用当前（默认）身份看基线
  python helpers/perm_probe.py --pool --sample-invoice 24000000000000000001

  # 2) 换成员工身份再跑一遍（环境变量注入，别改全局配置）
  KINGDEE_USERNAME=钱八 KINGDEE_PASSWORD=*** python helpers/perm_probe.py --pool

  # 3) 比"员工能不能看到这张票"最快的办法：
  python helpers/perm_probe.py --by-invoice 24000000000000000001 --by-bill SPD00008770

输出示例见 SKILL.md「收票池可见性」一节。**对照结论必须用同一个 filter/top 跑两次身份**，
否则数字不可比（本技能的教训：一次 top=2000 一次 top=1000，结论直接反了）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recvin_link import (Kingdee, load_kingdee_config, FORM_RECV_INV,  # noqa: E402
                         FORM_EXPENSE, FORM_TRAVEL)

# 收票单上"能解释可见性差异"的字段（实测：判别字段是 FSOURCEORGID）
DIAG_FIELDS = ('FBillNo,FIVNUMBER,FSUMALLAMOUNT,FGENERATETYPE,FISEXAMINE,'
               'FCreatorId,FMODIFIERID,FCreateDate,FSOURCEORGID.FNumber,'
               'FSETTLEORGID.FNumber,FPIAOZONESERIALNUMBER')


def main():
    ap = argparse.ArgumentParser(description="金蝶数据权限可见性实测（只读）")
    ap.add_argument("--pool", action="store_true", help="统计收票池可见规模 + 按制单人/组织分布")
    ap.add_argument("--top", type=int, default=2000, help="池子取样上限（默认 2000）")
    ap.add_argument("--by-invoice", action="append", default=[], help="按发票号回查（可重复）")
    ap.add_argument("--by-bill", action="append", default=[], help="按收票单号回查（可重复）")
    ap.add_argument("--bills", action="store_true", help="统计报销单可见规模（看是否被连带放开）")
    ap.add_argument("--fields", action="store_true", help="字段探活：打印收票单上真实存在的字段")
    ap.add_argument("--json", help="把结果额外写到该 JSON 文件，便于两次身份对照")
    a = ap.parse_args()

    cfg = load_kingdee_config()
    kd = Kingdee(cfg)
    ident = cfg.get("username")
    out = {"identity": ident, "base_url": cfg.get("base_url")}

    print("=" * 96)
    print("身份：%s   账套：%s" % (ident, cfg.get("base_url")))
    print("登录方式：账号口令登录（ValidateUser）—— 本 skill 只有这一个登录入口")
    print("=" * 96)

    if a.fields:
        print("\n【字段探活】收票单 IV_ReceivedInvoice")
        for f in ('FID', 'FBillNo', 'FIVNUMBER', 'FSUMALLAMOUNT', 'FGENERATETYPE', 'FISEXAMINE',
                  'FDOCUMENTSTATUS', 'FCreatorId', 'FMODIFIERID', 'FCreateDate', 'FMODIFYDATE',
                  'FSOURCEORGID.FNumber', 'FSETTLEORGID.FNumber', 'FBillTypeID.FNumber',
                  'FPIAOZONESERIALNUMBER'):
            try:
                kd.query(FORM_RECV_INV, 'FBillNo,' + f, '', top=1)
                print("   ✅ %s" % f)
            except (Exception, SystemExit) as e:
                print("   ❌ %s → %s" % (f, str(e)[:70]))

    if a.pool:
        print("\n【收票池可见规模】")
        try:
            rows = kd.query(FORM_RECV_INV, 'FBillNo,FCreatorId,FSOURCEORGID.FNumber',
                            '', top=a.top, order='FCreateDate desc')
            from collections import Counter
            by_creator = Counter(str(r[1]) for r in rows)
            by_org = Counter(str(r[2]) for r in rows)
            print("   共见 %d 张（取样上限 %d）" % (len(rows), a.top))
            print("   按制单人：%d 个 → %s" % (
                len(by_creator),
                ", ".join("%s:%d" % (k, v) for k, v in by_creator.most_common(12))))
            print("   按来源组织：%d 个 → %s" % (
                len(by_org),
                ", ".join("%s:%d" % (k, v) for k, v in by_org.most_common(12))))
            print("\n   判定参考：")
            print("     · 只见 1 个制单人、张数 = 自己的张数 → 数据规则 = 「只能查看自己单据」")
            print("     · 见多个制单人但**只有 1 个来源组织** → 规则被放成「整组织可见」⚠️（同事私有票也露）")
            print("     · 见多个组织 → 基本等于全池（本职级不该给员工）")
            if len(by_org) == 1 and len(by_creator) > 3:
                print("     ⇒ 本账号属于第 2 种：**能看同事的私有票**。")
            out["pool"] = {"count": len(rows), "creators": dict(by_creator), "orgs": dict(by_org)}
        except (Exception, SystemExit) as e:
            print("   ❌ 查询失败：%s" % str(e)[:200])

    if a.by_invoice or a.by_bill:
        print("\n【定点回查】")
        print("   %-24s %-13s %s" % ("发票号 / 单据号", "结果", "明细"))
        for no in a.by_invoice:
            hit, info = _q(kd, "FIVNUMBER='%s'" % no)
            print("   %-24s %-13s %s" % (no, "✅ 可见" if hit else "❌ 不可见", info))
        for bn in a.by_bill:
            hit, info = _q(kd, "FBillNo='%s'" % bn)
            print("   %-24s %-13s %s" % (bn, "✅ 可见" if hit else "❌ 不可见", info))
        # 敏感度校验：查一个必然不存在的号码，确认"不可见"不是接口异常
        try:
            z = kd.query(FORM_RECV_INV, 'FBillNo', "FIVNUMBER='00000000000000000000'", top=1)
            print("   （敏感度校验：查询不存在的发票号返回 %d 行 → 0 表示查询本身正常）" % len(z))
        except (Exception, SystemExit) as e:
            print("   （敏感度校验失败：%s）" % str(e)[:120])

    if a.bills:
        print("\n【报销单可见规模】")
        for form, label in ((FORM_TRAVEL, '差旅费报销单'), (FORM_EXPENSE, '费用报销单')):
            try:
                rows = kd.query(form, 'FID,FBillNo,FCreatorId', '', top=a.top, order='FID desc')
                from collections import Counter
                c = Counter(str(r[2]) for r in rows)
                print("   %s：共见 %d 张 / %d 个制单人 → %s" % (
                    label, len(rows), len(c),
                    ", ".join("%s:%d" % (k, v) for k, v in c.most_common(8))))
            except (Exception, SystemExit) as e:
                print("   %s：查询失败 %s" % (label, str(e)[:120]))
        print("   ⇒ 若只有一个制单人（自己）→ 收票单权限的改动**没有连带**放开报销单 ✅")

    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print("\n已写 %s（拿两次身份的结果对比即可）" % a.json)


def _q(kd, flt):
    try:
        rows = kd.query(FORM_RECV_INV, DIAG_FIELDS, flt, top=3)
    except (Exception, SystemExit) as e:
        return False, "查询异常 " + str(e)[:90]
    if not rows:
        return False, "查不到（不可见，或该票不存在）"
    i = DIAG_FIELDS.split(',')
    r = rows[0]
    d = dict(zip(i, r))
    return True, "单据=%s 金额=%s 组织=%s 制单=%s 流水号=%s FX=%s" % (
        d.get('FBillNo'), d.get('FSUMALLAMOUNT'), d.get('FSOURCEORGID.FNumber'),
        d.get('FCreatorId'), '有' if str(d.get('FPIAOZONESERIALNUMBER') or '').strip() else '无',
        d.get('FISEXAMINE'))


if __name__ == '__main__':
    main()
