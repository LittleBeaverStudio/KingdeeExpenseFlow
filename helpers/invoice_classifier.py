# -*- coding: utf-8 -*-
"""
发票 vs 行程单分类器 + 收票信息/附件分流建议 + 双算预警。

输入：OCR 文本（字符串）。WorkBuddy 端可用多模态读取图片后，把文本传入本模块。
输出：结构化字段 + 分类 + 建议上传位置 + 风险提醒。
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ParsedDoc:
    doc_type: str = "unknown"          # "invoice" | "itinerary" | "unknown"
    confidence: str = "low"            # "high" | "medium" | "low"
    raw_text: str = ""
    # 发票字段
    invoice_number: Optional[str] = None
    invoice_code: Optional[str] = None
    issue_date: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_tax_number: Optional[str] = None
    seller_name: Optional[str] = None
    seller_tax_number: Optional[str] = None
    amount_excl_tax: Optional[float] = None
    tax_amount: Optional[float] = None
    amount_incl_tax: Optional[float] = None
    tax_rate: Optional[float] = None
    items: List[dict] = field(default_factory=list)
    # 行程单字段
    itinerary_type: Optional[str] = None   # "flight" | "train" | "hotel" | "unknown"
    passenger_name: Optional[str] = None
    routes: List[dict] = field(default_factory=list)
    itinerary_total: Optional[float] = None


def _amount_to_float(s: str) -> Optional[float]:
    """把 ¥1,234.56 或 1234.56 转成 float。"""
    if s is None:
        return None
    s = s.replace("¥", "").replace(",", "").replace(" ", "")
    try:
        return float(s)
    except Exception:
        return None


def _extract_date(text: str) -> Optional[str]:
    """提取开票日期，统一成 YYYY-MM-DD。"""
    # 2026年08月26日
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 2026-08-26 / 2026/08/26
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _extract_buyer_seller(text: str) -> tuple:
    """提取购销方名称和税号。"""
    buyer_name = buyer_tax = seller_name = seller_tax = None

    # 购方名称（允许跨行）
    m = re.search(r"(?:购[买方]*|购买方).*?(?:名称|名\s*称)[:：]?\s*([^\n]{2,60})", text, re.S)
    if m:
        buyer_name = m.group(1).strip().split("\n")[0].strip()
    # 购方税号（允许跨行）
    m = re.search(r"(?:购[买方]*|购买方).*?(?:纳税人识别号|税号|统一社会信用代码)[:：]?\s*([A-Z0-9]{15,20})", text, re.S)
    if m:
        buyer_tax = m.group(1).strip()

    # 销售方名称（允许跨行）
    m = re.search(r"(?:销[售方]*|销售方).*?(?:名称|名\s*称)[:：]?\s*([^\n]{2,60})", text, re.S)
    if m:
        seller_name = m.group(1).strip().split("\n")[0].strip()
    # 销售方税号（允许跨行）
    m = re.search(r"(?:销[售方]*|销售方).*?(?:纳税人识别号|税号|统一社会信用代码)[:：]?\s*([A-Z0-9]{15,20})", text, re.S)
    if m:
        seller_tax = m.group(1).strip()

    return buyer_name, buyer_tax, seller_name, seller_tax


def _extract_amounts(text: str) -> dict:
    """从发票文本提取金额。"""
    out = {"excl": None, "tax": None, "incl": None, "rate": None}

    # 价税合计（大写）后的 小写金额
    m = re.search(r"价税合计.*?(?:小写|￥|¥)\s*([\d,]+\.?\d*)", text, re.S)
    if m:
        out["incl"] = _amount_to_float(m.group(1))

    # 合计行：金额 ... 税额
    # 典型： 合计    ¥10.00    ¥0.30
    m = re.search(r"合\s*计.*?([\d,]+\.?\d*)\s+([\d,]+\.?\d*)", text, re.S)
    if m:
        out["excl"] = _amount_to_float(m.group(1))
        out["tax"] = _amount_to_float(m.group(2))

    # 税率
    m = re.search(r"(\d{1,2})\s*%", text)
    if m:
        out["rate"] = float(m.group(1))

    # 如果只有价税合计，按税率倒推
    if out["incl"] and out["rate"] and out["excl"] is None:
        out["excl"] = round(out["incl"] / (1 + out["rate"] / 100), 2)
        if out["tax"] is None:
            out["tax"] = round(out["incl"] - out["excl"], 2)

    return out


def _extract_invoice_number(text: str) -> Optional[str]:
    m = re.search(r"发票号码?[:：]?\s*([\d]{10,25})", text)
    if m:
        return m.group(1).strip()
    # 电子发票右上角常是 20 位
    m = re.search(r"\b([\d]{20})\b", text)
    if m:
        return m.group(1).strip()
    return None


def _extract_invoice_code(text: str) -> Optional[str]:
    m = re.search(r"发票代码?[:：]?\s*([\d]{10,12})", text)
    if m:
        return m.group(1).strip()
    return None


def _extract_itinerary_type(text: str) -> str:
    text = text.upper()
    if re.search(r"航\s*班|机\s*票|航\s*空|乘机|起\s*飞|降\s*落|PNR|ETKT", text):
        return "flight"
    if re.search(r"车\s*次|火\s*车|高\s*铁|动\s*车|乘\s*车|二等座|一等座", text):
        return "train"
    if re.search(r"住\s*宿|酒\s*店|入\s*住|离\s*店|房\s*费", text):
        return "hotel"
    return "unknown"


def _extract_itinerary_total(text: str) -> Optional[float]:
    """行程单总金额：优先匹配「合计金额」「总金额」「票价+税费」等。"""
    # 机票行程单：合计金额
    m = re.search(r"合计金额[:：]?\s*¥?\s*([\d,]+\.?\d*)", text)
    if m:
        return _amount_to_float(m.group(1))
    m = re.search(r"总金额[:：]?\s*¥?\s*([\d,]+\.?\d*)", text)
    if m:
        return _amount_to_float(m.group(1))
    # 火车票：票价
    m = re.search(r"票\s*价[:：]?\s*¥?\s*([\d,]+\.?\d*)", text)
    if m:
        return _amount_to_float(m.group(1))
    # 滴滴发票本质是发票，但带出行信息；这里不做行程单总金额兜底
    return None


def _extract_passenger(text: str) -> Optional[str]:
    m = re.search(r"旅\s*客[:：]?\s*([^\n\s]{1,20})", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"姓\s*名[:：]?\s*([^\n\s]{1,20})", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"出\s*行\s*人[:：]?\s*([^\n\s]{1,20})", text)
    if m:
        return m.group(1).strip()
    return None


def _extract_routes(text: str, itin_type: str) -> List[dict]:
    routes = []
    if itin_type == "flight":
        # 航班号 + 出发/到达城市/机场
        for m in re.finditer(r"([A-Z]{2}\d{3,4})\s*[^\n]*?(\S{2,6})\s*[-→~到]\s*(\S{2,6})", text):
            routes.append({
                "flight_no": m.group(1),
                "from": m.group(2),
                "to": m.group(3),
            })
    elif itin_type == "train":
        for m in re.finditer(r"([GDCZTK]\d{1,5})\s*[^\n]*?(\S{2,6})\s*[-→~到]\s*(\S{2,6})", text):
            routes.append({
                "train_no": m.group(1),
                "from": m.group(2),
                "to": m.group(3),
            })
    return routes


def classify(text: str) -> ParsedDoc:
    """对 OCR 文本进行分类与字段提取。"""
    text = text or ""
    doc = ParsedDoc(raw_text=text)

    # 发票强特征
    has_inv_number = bool(re.search(r"发票号码?", text))
    has_tax_number = bool(re.search(r"纳税人识别号|统一社会信用代码", text))
    has_amount_incl = bool(re.search(r"价税合计", text))
    has_einvoice_stamp = bool(re.search(r"电子发票|电子普通发票|增值税电子", text))

    # 行程单强特征
    itin_type = _extract_itinerary_type(text)
    has_flight_train = itin_type in ("flight", "train")

    # 决策
    if (has_inv_number or has_tax_number or has_amount_incl or has_einvoice_stamp) and not has_flight_train:
        doc.doc_type = "invoice"
        doc.confidence = "high" if has_inv_number and has_tax_number else "medium"
    elif has_flight_train and not (has_inv_number or has_amount_incl):
        doc.doc_type = "itinerary"
        doc.confidence = "high"
    elif has_flight_train and has_amount_incl:
        # 例如滴滴电子发票，带「旅客运输服务」和出行人空表，但本质是发票
        doc.doc_type = "invoice"
        doc.confidence = "high"
    else:
        doc.doc_type = "unknown"
        doc.confidence = "low"

    # 提取发票字段
    if doc.doc_type == "invoice":
        doc.invoice_number = _extract_invoice_number(text)
        doc.invoice_code = _extract_invoice_code(text)
        doc.issue_date = _extract_date(text)
        doc.buyer_name, doc.buyer_tax_number, doc.seller_name, doc.seller_tax_number = _extract_buyer_seller(text)
        amounts = _extract_amounts(text)
        doc.amount_excl_tax = amounts.get("excl")
        doc.tax_amount = amounts.get("tax")
        doc.amount_incl_tax = amounts.get("incl")
        doc.tax_rate = amounts.get("rate")

    # 提取行程单字段
    if doc.doc_type == "itinerary":
        doc.itinerary_type = itin_type
        doc.passenger_name = _extract_passenger(text)
        doc.routes = _extract_routes(text, itin_type)
        doc.itinerary_total = _extract_itinerary_total(text)

    return doc


def recommend_upload_position(doc: ParsedDoc) -> tuple:
    """
    返回 (建议位置, 原因, 风险等级)。
    位置："rec_inv_info" 收票信息 | "attachment" 附件 | "manual_review" 人工复核
    """
    if doc.doc_type == "invoice":
        return ("rec_inv_info", "识别为发票，应传入收票信息参与金额校验", "none")
    if doc.doc_type == "itinerary":
        return ("attachment", "识别为行程单，传入附件即可；传入收票信息会导致金额重复计入", "high")
    return ("manual_review", "无法识别类型，建议人工确认", "medium")


def check_double_counting(docs: List[ParsedDoc]) -> dict:
    """
    当用户把多个文件都上传至收票信息时，检查是否有行程单混入并计算汇总金额。
    """
    invoices = [d for d in docs if d.doc_type == "invoice"]
    itineraries = [d for d in docs if d.doc_type == "itinerary"]

    invoice_total = sum((d.amount_incl_tax or 0) for d in invoices)
    itinerary_total = sum((d.itinerary_total or 0) for d in itineraries)
    combined_total = invoice_total + itinerary_total

    warnings = []
    if itineraries:
        warnings.append(
            f"检测到 {len(itineraries)} 张行程单（合计约 ¥{itinerary_total:.2f}）。"
            f"行程单应传附件；若传入收票信息，会与发票金额（¥{invoice_total:.2f}）叠加，"
            f"汇总金额将虚高至 ¥{combined_total:.2f}，可能被财务驳回。"
        )

    return {
        "invoice_count": len(invoices),
        "itinerary_count": len(itineraries),
        "invoice_total": invoice_total,
        "itinerary_total": itinerary_total,
        "combined_total": combined_total,
        "warnings": warnings,
        "has_risk": bool(itineraries),
    }


def validate_invoice_against_org(doc: ParsedDoc, org_name: str, org_tax_number: Optional[str] = None) -> List[str]:
    """校验发票购方抬头是否匹配申请组织。"""
    errs = []
    if doc.doc_type != "invoice":
        return errs
    if doc.buyer_name and org_name not in doc.buyer_name and doc.buyer_name not in org_name:
        errs.append(f"购方抬头「{doc.buyer_name}」与申请组织「{org_name}」不一致")
    if org_tax_number and doc.buyer_tax_number and org_tax_number != doc.buyer_tax_number:
        errs.append(f"购方税号「{doc.buyer_tax_number}」与组织税号「{org_tax_number}」不一致")
    return errs


if __name__ == "__main__":
    # 简单自测：用本文件运行时传入一个 txt 文件路径
    import sys
    if len(sys.argv) > 1:
        txt = open(sys.argv[1], "r", encoding="utf-8").read()
        d = classify(txt)
        print("分类:", d.doc_type, "置信度:", d.confidence)
        print("建议位置:", recommend_upload_position(d))
        print("字段:", d)
    else:
        print("用法: python invoice_classifier.py <ocr_text_file>")
