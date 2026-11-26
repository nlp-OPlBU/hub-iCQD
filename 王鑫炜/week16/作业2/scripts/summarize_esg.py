#!/usr/bin/env python3
"""Generate local Markdown digests from search candidates or verified ESG evidence."""

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import html
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CST = timezone(timedelta(hours=8))
MONTHLY_SECTIONS = ("政策与标准", "行业与最佳实践", "公司动态", "同业行动")
MONTHLY_HINTS = {
    "政策与标准": ("发布", "实施", "生效", "标准", "披露", "征求意见"),
    "行业与最佳实践": ("效率", "投运", "电力", "煤电", "排放", "能耗"),
    "公司动态": ("公告", "收购", "决议", "报告", "产量", "安全"),
    "同业行动": ("试点", "投运", "启动", "减排", "达产", "项目"),
}
DEFAULT_BINS_WAN = (Decimal("20"), Decimal("50"), Decimal("100"), Decimal("300"))
NOTICE_PRIORITY = {"tender": 0, "amendment": 1, "award": 2, "cancelled": 3}


def load_object(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON 顶层必须为对象")
    return data


def write_output(path, content):
    if path is None:
        print(content, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary_path = Path(handle.name)
        try:
            handle.write(content)
            handle.close()
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def string(record, key):
    value = record.get(key) if isinstance(record, dict) else None
    return " ".join(value.split()) if isinstance(value, str) else ""


def md(value):
    return re.sub(r"([\\`*_\[\]|~])", r"\\\1", html.escape(str(value), quote=False))


def safe_url(raw_url):
    try:
        parts = urlsplit(raw_url)
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.hostname or parts.username or parts.password
            or any(char.isspace() or char in '<>\\' for char in raw_url)
        ):
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


def canonical_url(url):
    parts = urlsplit(url)
    pairs = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(("", parts.netloc.lower(), parts.path or "/", urlencode(pairs), ""))


def source(record):
    return f"[{md(record['id'])} · {md(record['publisher'])}](<{record['url']}>)"


def excerpt(text, hints=(), limit=170):
    sentences = [
        piece.strip()
        for piece in re.split(r"(?<=[。！？；])\s*|(?<=[.!?;])\s+", text)
        if piece.strip()
    ]
    if not sentences:
        return "（无文本）"

    def priority(position):
        sentence = sentences[position]
        return (
            sum(hint.casefold() in sentence.casefold() for hint in hints)
            + bool(re.search(r"\d+(?:\.\d+)?\s*(?:万吨|万元|亿元|%|兆瓦|千瓦|吨|元)", sentence)),
            -position,
        )

    selected = sorted(sorted(range(len(sentences)), key=priority, reverse=True)[:2])
    summary = " ".join(sentences[position] for position in selected)
    return summary if len(summary) <= limit else summary[:limit].rstrip() + "…"


def evidence(data, expected_case):
    if data.get("case") != expected_case:
        raise ValueError(f"case 应为 {expected_case}")
    start, end, cutoff = (date.fromisoformat(data[key]) for key in ("start", "end", "as_of"))
    if start > end or end > cutoff:
        raise ValueError("日期必须满足 start ≤ end ≤ as_of")
    if not isinstance(data.get("records"), list):
        raise ValueError("records 必须为数组")

    accepted, audit = [], []
    ids = set()
    for index, row in enumerate(data["records"], 1):
        if not isinstance(row, dict):
            audit.append((f"行 {index}", "记录类型错误"))
            continue
        identifier = string(row, "id") or f"行 {index}"
        try:
            published = date.fromisoformat(string(row, "date"))
        except ValueError:
            audit.append((identifier, "公告或来源日期缺失/无效"))
            continue
        url = safe_url(string(row, "url"))
        required = ("id", "title", "category", "publisher", "text")
        if not url or not all(string(row, key) for key in required):
            audit.append((identifier, "原文 URL 或基本字段缺失/无效"))
            continue
        if published > cutoff:
            audit.append((identifier, "发布日期晚于证据截点"))
            continue
        if identifier in ids:
            raise ValueError(f"证据编号重复: {identifier}")
        ids.add(identifier)
        normalized = {key: string(row, key) for key in required}
        accepted.append({**row, **normalized, "published": published, "url": url})
    return start, end, cutoff, accepted, audit


def chronological(row):
    return row["published"], row["id"]


def deduplicate(records, key, audit, reason, order=chronological):
    retained = {}
    for record in sorted(records, key=order):
        identity = key(record)
        previous = retained.get(identity)
        if previous:
            audit.append((previous["id"], f"{reason}，保留 {record['id']}"))
        retained[identity] = record
    return sorted(retained.values(), key=chronological)


def audit_section(audit):
    lines = ["", "## 逐条筛选记录", ""]
    if audit:
        lines += [f"- {md(identifier)} · {md(reason)}" for identifier, reason in sorted(audit)]
    else:
        lines.append("本模式输入无剔除记录。")
    return lines


def render_candidates(data, limit):
    rows = data.get("results")
    errors = data.get("errors")
    if not isinstance(rows, list) or not isinstance(errors, list):
        raise ValueError("搜索快照须包含 results 和 errors 数组")
    status = string(data, "status")
    lines = [
        "# ESG 网络检索线索",
        "",
        f"搜索来源 · {md(string(data, 'provider'))}。请求状态 · {md(status)}。",
        f"抓取时间 · {md(string(data, 'retrieved_at')) or '未记录'}。",
        "",
        "下列内容为搜索引擎摘要。来源、公告状态、日期和金额须打开原文核验。",
        "",
    ]
    for error in errors:
        lines.append(f"- 搜索失败 · {md(string(error, 'query'))} · {md(string(error, 'message'))}")
    seen = set()
    for row in rows:
        title = string(row, "title")
        url = safe_url(string(row, "url"))
        if not title or not url or canonical_url(url) in seen:
            continue
        seen.add(canonical_url(url))
        content = md(excerpt(string(row, "description"), limit=100))
        link_note = " · 聚合链接" if row.get("is_aggregator") is True else ""
        lines.append(f"- [{md(title)}](<{url}>){link_note} · {content}")
        query = string(row, "query")
        if query:
            lines.append(f"  - 查询词 · {md(query)}")
        if len(seen) >= limit:
            break
    if not seen:
        lines.append("快照无可渲染线索。")
    return lines


def render_monthly(data):
    start, end, cutoff, rows, audit = evidence(data, "monthly")
    in_scope = []
    for row in rows:
        if not start <= row["published"] <= end:
            audit.append((row["id"], "事实日期超出月报窗口"))
        elif row["category"] not in MONTHLY_SECTIONS:
            audit.append((row["id"], "主题未映射到月报章节"))
        else:
            in_scope.append(row)

    selected = deduplicate(in_scope, lambda row: canonical_url(row["url"]), audit, "重复来源")
    subject = md(string(data, "subject") or "客户")
    lines = [
        f"# {subject} ESG 月报 · 自动证据摘要",
        "",
        f"观察期 {start} 至 {end}，证据截点 {cutoff}，纳入 {len(selected)} 条。",
        "",
        "下列摘要从已核验证据摘取事实。公司影响、同业可比性及行动优先级仍需研判。",
    ]
    for section in MONTHLY_SECTIONS:
        lines += ["", f"## {section}", ""]
        matched = [row for row in selected if row["category"] == section]
        if not matched:
            lines.append("本批材料无该主题的合格证据。")
        for row in matched:
            basis = string(row, "date_basis")
            date_label = f"{row['published']}（{md(basis)}）" if basis else str(row["published"])
            lines.append(f"- {date_label} · **{md(row['title'])}** · {source(row)}")
            lines.append(f"  - {md(excerpt(row['text'], MONTHLY_HINTS[section]))}")
    return lines + audit_section(audit)


def project_identity(row):
    project_id = string(row, "project_id").casefold()
    if project_id:
        return ("project", project_id)
    return ("url", canonical_url(row["url"]))


def project_key(row):
    return (*project_identity(row), string(row, "lot").casefold())


def package_identity(row):
    return " / ".join(filter(None, (string(row, "project_id"), string(row, "lot"))))


def award_amount(row):
    if row.get("amount_kind") != "award_total":
        return None
    value = string(row, "amount_cny")
    if not re.fullmatch(r"[0-9]{1,15}(?:\.[0-9]{1,2})?", value):
        return None
    return Decimal(value)


def median(values):
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def render_awards(data, bins_wan=DEFAULT_BINS_WAN):
    if not bins_wan or any(
        not cut.is_finite() or not 0 < cut <= 10**11 for cut in bins_wan
    ) or tuple(sorted(set(bins_wan))) != tuple(bins_wan):
        raise ValueError("--bins-wan 必须为从小到大排列的正数，单位为万元")
    start, end, cutoff, rows, audit = evidence(data, "procurement")
    in_scope = []
    for row in rows:
        if not start <= row["published"] <= end:
            audit.append((row["id"], "来源日期超出统计窗口"))
        elif row.get("notice_type") != "award":
            audit.append((row["id"], f"公告类型 {string(row, 'notice_type') or '未知'} 不计入成交"))
        else:
            in_scope.append(row)
    selected = deduplicate(in_scope, project_key, audit, "重复项目包")
    amounts = [award_amount(row) for row in selected]
    known = sorted(amount for amount in amounts if amount is not None)
    counts = Counter(row["category"] for row in selected)
    projects = {project_identity(row) for row in selected}
    thematic_projects = Counter(
        category for category, _ in {(row["category"], project_identity(row)) for row in selected}
    )
    lines = [
        "# ESG 招投标 · 中标样本自动摘要",
        "",
        f"观察期 {start} 至 {end}，证据截点 {cutoff}。纳入 {len(selected)} 条中标结果，涉及 {len(projects)} 个项目。",
        f"可比人民币成交总价 {len(known)} 条，缺少可比金额 {len(selected) - len(known)} 条。",
        "统计以去重后的已核验样本为分母。金额统计仅使用人民币成交总价。",
        "",
        "## 主题频次",
        "",
    ]
    lines += [
        f"- {md(category)} · {count} 条，{thematic_projects[category]} 个项目"
        for category, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ] or ["样本为空。"]
    lines += ["", "## 成交金额分布", ""]
    if known:
        total = sum(known)
        lines.append(
            f"合计 {total:,.2f} 元，中位数 {median(known):,.3f} 元，"
            f"区间 {known[0]:,.2f} 至 {known[-1]:,.2f} 元。"
        )
        totals_by_project = Counter()
        names_by_project = {}
        for row, amount in zip(selected, amounts):
            if amount is not None:
                identity = project_identity(row)
                totals_by_project[identity] += amount
                names_by_project[identity] = string(row, "project_id") or row["title"]
        if total:
            biggest, known_amount = totals_by_project.most_common(1)[0]
            lines.append(
                f"已知金额最高的项目为 {md(names_by_project[biggest])}，可比金额 {known_amount:,.2f} 元，"
                f"占样本已知金额 {known_amount / total:.2%}。"
            )
    else:
        lines.append("本批样本无可比成交总价。")
    lines += ["", "| 金额分组 | 中标数 |", "| --- | ---: |"]
    display = [f"{cut:f} 万元" for cut in bins_wan]
    labels = [f"<{display[0]}"]
    labels.extend(f"{lo} ≤ 金额 < {hi}" for lo, hi in zip(display, display[1:]))
    labels.append(f"≥{display[-1]}")
    limits = [Decimal(0), *(cut * 10000 for cut in bins_wan), Decimal("Infinity")]
    for low, high, label in zip(limits, limits[1:], labels):
        count = sum(low <= amount < high for amount in known)
        lines.append(f"| {label} | {count} |")

    lines += ["", "## 逐包来源", ""]
    for row, amount in zip(selected, amounts):
        amount_text = f"{amount:,.2f} 元" if amount is not None else "不可比或缺失"
        package = md(package_identity(row) or "无可用项目包标识")
        lines.append(f"- {row['published']} · {md(row['title'])} · {package} · {amount_text} · {source(row)}")
        lines.append(f"  - {md(excerpt(row['text'], ('中标', '成交', '供应商', '采购', '服务')))}")
    return lines + audit_section(audit)


def deadline_status(deadline, at):
    try:
        if len(deadline) == 10:
            deadline_day = date.fromisoformat(deadline)
            if deadline_day == at.date():
                return "截止日为截点当天，具体时间待核验"
            return "开放" if deadline_day > at.date() else "已截止"
        moment = datetime.fromisoformat(deadline)
        if moment.tzinfo is not None:
            return "开放" if moment > at else "已截止"
    except ValueError:
        pass
    return "截止时间或时区待核验"


def notice_order(row):
    # When the exact sequence on the same day is unknown, prefer the more restrictive update.
    return row["published"], NOTICE_PRIORITY[row["notice_type"]], row["id"]


def publication_status(row, at):
    published_at = string(row, "published_at")
    if published_at:
        try:
            moment = datetime.fromisoformat(published_at)
            if moment.tzinfo is not None and moment.astimezone(CST).date() == row["published"]:
                return "future" if moment > at else "confirmed"
        except ValueError:
            pass
    if row["published"] == at.date() and at.time() < time(23, 59, 59):
        return "time_unknown"
    return "confirmed"


def render_daily(data, since, at):
    _, _, cutoff, rows, audit = evidence(data, "procurement")
    if at is not None and at.tzinfo is None:
        raise ValueError("--at 必须含时区")
    at = at.astimezone(CST) if at else datetime.combine(cutoff, time(23, 59, 59), CST)
    since = since or at.date()
    if since > at.date() or at.date() > cutoff:
        raise ValueError("日报起日 ≤ 日报截点日 ≤ as_of")

    relevant = []
    for row in rows:
        kind = string(row, "notice_type")
        if row["published"] > at.date() or publication_status(row, at) == "future":
            audit.append((row["id"], "晚于日报判断截点"))
        elif kind == "candidate" and since <= row["published"]:
            audit.append((row["id"], "候选公示不作为开放招标"))
        elif kind in NOTICE_PRIORITY:
            relevant.append(row)

    versions = deduplicate(relevant, project_key, audit, "存在优先的已核验公告", notice_order)
    actionable, review = [], []
    for row in versions:
        kind = string(row, "notice_type")
        if row["published"] < since:
            if kind == "tender":
                audit.append((row["id"], "招标发布日期早于新增窗口"))
            continue
        if kind in {"award", "cancelled"}:
            audit.append((row["id"], "中标或终止公告不作为开放招标"))
            continue
        if publication_status(row, at) == "time_unknown":
            review.append((row, "缺少当日具体发布时间，须核验是否早于判断截点"))
            continue
        if kind == "amendment":
            review.append((row, "更正公告，需核验当前招标状态和截止时间"))
            continue
        if row.get("status") == "closed":
            audit.append((row["id"], "核验状态为关闭"))
            continue
        status = deadline_status(string(row, "deadline"), at)
        registration = string(row, "registration_deadline")
        registration_status = deadline_status(registration, at) if registration else None
        if status == "已截止":
            audit.append((row["id"], status))
        elif registration_status == "已截止":
            audit.append((row["id"], "报名或文件获取已截止"))
        elif registration_status and registration_status != "开放":
            review.append((row, f"报名或文件获取 · {registration_status}"))
        elif row.get("needs_registration") is True and not registration:
            review.append((row, "报名或文件获取截止时间待核验"))
        elif row.get("status") != "open":
            review.append((row, "当前招标状态待核验"))
        elif status == "开放":
            actionable.append(row)
        else:
            review.append((row, status))

    lines = [
        "# ESG 招标监测 · 每日推送稿",
        "",
        f"新增公告窗口 {since} 至 {at.date()}，判断截点（北京时间）{at.isoformat()}。",
        f"本批合格的开放招标 {len(actionable)} 条，待人工核验 {len(review)} 条。",
        "",
        "## 可跟进公告",
        "",
    ]
    if not actionable:
        lines.append("本批来源无可以确认且仍开放的新增公告。")
    for row in actionable:
        title = md(row["title"])
        buyer = md(string(row, "buyer") or "采购人待核验")
        deadline = md(string(row, "deadline"))
        registration = string(row, "registration_deadline")
        registration_note = f" · 获取/报名截至 {md(registration)}" if registration else ""
        lines.append(f"- {row['published']} · **{title}** · {buyer} · 投标截止 {deadline}{registration_note} · {source(row)}")
        lines.append(f"  - {md(excerpt(row['text'], ('招标', '投标', '采购', '服务')))}")
    lines += ["", "## 待核验线索", ""]
    lines += [
        f"- {md(row['title'])} · {md(reason)} · {source(row)}"
        for row, reason in review
    ] or ["本批无待核验线索。"]
    return lines + audit_section(audit)


def decimal_argument(value):
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", value):
        raise argparse.ArgumentTypeError(f"无效的十进制金额档位: {value}")
    return Decimal(value)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("candidates", "monthly", "awards", "daily"):
        subparser = modes.add_parser(mode)
        subparser.add_argument("input", type=Path, help="搜索快照或已核验证据 JSON")
        subparser.add_argument("--out", type=Path, help="Markdown 输出路径，省略时写到终端")
        if mode == "candidates":
            subparser.add_argument("--limit", type=int, choices=range(1, 51), default=12)
        if mode == "awards":
            subparser.add_argument("--bins-wan", nargs="+", default=DEFAULT_BINS_WAN, type=decimal_argument)
        if mode == "daily":
            subparser.add_argument("--since", type=date.fromisoformat)
            subparser.add_argument("--at", type=datetime.fromisoformat)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        data = load_object(args.input)
        if args.mode == "candidates":
            lines = render_candidates(data, args.limit)
        elif args.mode == "monthly":
            lines = render_monthly(data)
        elif args.mode == "awards":
            lines = render_awards(data, args.bins_wan)
        else:
            lines = render_daily(data, args.since, args.at)
        write_output(args.out, "\n".join(lines) + "\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"摘要生成失败 · {error}") from error


if __name__ == "__main__":
    main()
