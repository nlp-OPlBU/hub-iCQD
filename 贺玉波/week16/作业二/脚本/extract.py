#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministic field extraction for Chinese public-tender notices.

Why a script: amounts, dates and party names are the part of the analysis that must be
numerically exact and reproducible. The LLM is not allowed to "guess" these fields —
it only reviews rows the script marks as low-confidence.

Extracted fields:
  project, buyer, supplier, amount_wan, date, category, buyer_type, region, confidence, evidence
"""
import re

UNIT_TO_WAN = {"亿元": 10000.0, "万元": 1.0, "元": 0.0001}

AMOUNT_KEYWORDS = ["中标（成交）金额", "中标(成交)金额", "中标（成交）价", "中标(成交)价",
                   "中标金额", "成交金额", "中标价", "成交价", "中标价格", "中选金额",
                   "合同金额", "成交价格", "报价", "中标（成交）报价", "成交报价"]

BUYER_KEYWORDS = ["采购单位", "采购人", "招标人", "采购方", "建设单位", "招标单位", "需求单位"]
SUPPLIER_KEYWORDS = ["中标供应商名称", "中标（成交）供应商名称", "成交供应商名称", "中标供应商", "成交供应商",
                     "中标人", "成交人", "中标单位", "成交单位", "中选供应商", "中标候选人",
                     "第一中标候选人", "第一成交候选人", "供应商名称"]


def clean(s):
    if not s:
        return ""
    s = s.replace("\u3000", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _num(s):
    try:
        return float(s.replace(",", "").replace("，", ""))
    except Exception:
        return None


def find_amount(text):
    """Return dict(amount_wan, raw, confidence) or None."""
    if not text:
        return None
    t = clean(text)
    for kw in AMOUNT_KEYWORDS:
        idx = 0
        while True:
            i = t.find(kw, idx)
            if i < 0:
                break
            seg = t[i + len(kw): i + len(kw) + 110]
            m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|万元|元)", seg)
            if m:
                v = _num(m.group(1))
                unit = m.group(2)
                if v and v > 0:
                    return {"amount_wan": round(v * UNIT_TO_WAN[unit], 2),
                            "raw": (kw + "：" + m.group(0))[:90],
                            "confidence": "high" if unit in ("亿元", "万元") else "medium"}
            if "金额" not in kw:
                break
            idx = i + 1
    # fallback: a bare number right after the word 金额
    m = re.search(r"金额[^0-9]{0,12}([0-9][0-9,]*(?:\.[0-9]+)?)", t)
    if m:
        v = _num(m.group(1))
        if v and v > 0:
            wan = v if v < 1000 else v * 0.0001
            conf = "low" if v < 1000 else "medium"
            return {"amount_wan": round(wan, 2), "raw": m.group(0)[:60], "confidence": conf}
    return None


def _take_value(text, keywords, stop=r"[\n，,；;。]|地址|金额|得分|联系"):
    for kw in keywords:
        # 1) colon style: "采购人：XXX"
        for m in re.finditer(re.escape(kw) + r"[^：:\n]{0,10}[：:]\s*", text):
            seg = text[m.end(): m.end() + 80]
            seg = re.split(r"[\n；;]", seg)[0]
            seg = re.split(r"(?:地址|金额|评审|得分|联系|电话|项目编号)", seg)[0]
            val = re.split(r"[：:，,；;]", clean(seg.strip(" ：:，,。.、")))[0].strip()
            if 2 <= len(val) <= 60 and not re.search(r"^(名称|地址|金额)$", val):
                return val
        # 2) table style: "采购单位\nXXX" / "中标供应商名称\nXXX"
        for m in re.finditer(re.escape(kw) + r"\s*\n\s*([^\n]{2,60})", text):
            val = re.split(r"[：:，,；;]", clean(m.group(1)).strip(" ：:，,。.、"))[0].strip()
            if 2 <= len(val) <= 60 and not re.search(r"地址|金额|得分|联系|电话|评审", val):
                return val
    return ""


ORG_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9（）()·&]{4,50}(?:股份有限公司|有限责任公司|有限公司|集团|研究院|设计院|事务所|中心|大学|学院|公司))")


def _looks_org(v):
    if not v or re.fullmatch(r"[\d.,%\s]+", v or ""):
        return False
    bad = ["供应商名称", "供应商地址", "采购人", "代理机构", "评审", "金额", "得分", "地址"]
    return not any(b in v for b in bad)


def find_supplier(text):
    v = _take_value(text, SUPPLIER_KEYWORDS)
    if _looks_org(v):
        return v
    # fallback: a company-like token inside the "中标（成交）信息" block
    i = text.find("中标（成交）信息")
    seg = text[i:i + 800] if i >= 0 else text[:2000]
    cands = [m.group(1) for m in ORG_RE.finditer(seg)]
    cands = [c for c in cands if _looks_org(c) and "招标" not in c and "采购" not in c]
    return cands[0] if cands else v


def find_buyer(text):
    return _take_value(text, BUYER_KEYWORDS)


def find_project(text, fallback_title=""):
    m = re.search(r"项目名称[^：:\n]{0,6}[：:]\s*([^\n，,；;]{4,90})", text)
    if m:
        return clean(m.group(1))
    t = re.sub(r"(中标|成交|结果|中标结果|成交结果|结果公告|结果公示|公告|公示|候选人)", "", fallback_title)
    return clean(t)[:90]


CATEGORY_RULES = [
    ("report", ["ESG报告", "ESG 报告", "社会责任报告", "可持续发展报告", "ESG信息披露", "环境、社会及管治", "环境社会及管治", "专项报告", "报告编制", "报告服务", "报告项目"]),
    ("consulting", ["ESG咨询", "ESG 咨询", "管理咨询", "治理体系", "ESG战略", "ESG提升", "ESG规划"]),
    ("platform", ["碳管理平台", "碳核算", "碳足迹", "能碳", "碳监测", "碳排放管理", "碳数据", "碳大脑"]),
    ("carbonasset", ["CCER", "碳资产", "碳汇", "减排量", "绿证"]),
    ("zerocarbon", ["零碳园区", "零碳工厂", "绿色工厂", "低碳园区", "近零碳", "零碳"]),
    ("greenfinance", ["绿色金融", "ESG评级", "ESG 评级", "转型金融", "绿色债券", "ESG评价"]),
    ("supplychain", ["绿色供应链", "绿色采购", "供应链ESG", "供应链 ESG"]),
    ("dualcarbon", ["碳达峰", "碳中和", "双碳", "十五五", "达峰", "节能降碳"]),
]


def classify(text, default="other"):
    best, best_hits = default, 0
    for cid, kws in CATEGORY_RULES:
        hits = sum(1 for k in kws if k in text)
        if hits > best_hits:
            best, best_hits = cid, hits
    return best


def buyer_type(buyer, text):
    b = buyer or ""
    t = text or ""
    if re.search(r"(市|区|县|州|盟|镇|局|委员会|管委会|人民政府|中心|学校|大学|医院|研究院)", b):
        return "政府/事业单位"
    if re.search(r"(集团|电网|能源|股份|有限公司|有限责任公司|控股)", b):
        if re.search(r"(国家能源|南方电网|国家电投|华能|华电|大唐|三峡|中广核|中石油|中石化|中车|华润)", b):
            return "央企/国企"
        return "企业"
    if "园区" in t and "政府" not in b:
        return "园区/平台公司"
    return "未识别"


def extract(title, text, url=""):
    """Extract one tender record. `text` may be page text or a search snippet."""
    body = clean((title or "") + "\n" + (text or ""))
    amt = find_amount(body)
    date = find_date(body)
    cat = classify(title or "", "other")
    if cat == "other":
        cat = classify(body)
    rec = {
        "project": find_project(body, title),
        "buyer": find_buyer(body),
        "supplier": find_supplier(body),
        "amount_wan": amt["amount_wan"] if amt else None,
        "amount_raw": amt["raw"] if amt else "",
        "date": date,
        "category": cat,
        "buyer_type": "",
        "region": "",
        "url": url,
        "confidence": "low",
        "evidence": body[:300].replace("\n", " "),
    }
    rec["buyer_type"] = buyer_type(rec["buyer"], body)
    if rec["amount_wan"] and rec["supplier"] and rec["date"]:
        rec["confidence"] = "high" if amt["confidence"] == "high" else "medium"
    elif rec["amount_wan"] and rec["date"]:
        rec["confidence"] = "medium" if amt["confidence"] != "low" else "low"
    return rec


DATE_RE = re.compile(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})")


def find_date(text):
    if not text:
        return ""
    best = ""
    for m in DATE_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2023 <= y <= 2026 and 1 <= mo <= 12 and 1 <= d <= 31:
            best = f"{y:04d}-{mo:02d}-{d:02d}"
            break
    return best
