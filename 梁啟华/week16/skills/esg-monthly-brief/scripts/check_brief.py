#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ESG 月报结构与质量门机器校验（仅标准库）。

用法：
    python -I scripts/check_brief.py <简报.md> [--min-sources 5]

说明：
    本脚本只能校验“结构与要素是否齐全”，无法判断数据真实性；
    事实核验仍需人工回溯来源。
"""

import argparse
import re
import sys

# 四个必需章节：显示名 -> 可接受的标题关键词
REQUIRED_SECTIONS = [
    ("政策与标准", ("政策与标准", "政策")),
    ("行业实践", ("行业实践",)),
    ("客户张力与同行对标", ("客户张力", "张力")),
    ("月度结论与跟踪点", ("月度结论", "结论与跟踪")),
]

PLACEHOLDER_RE = re.compile(r"(待补充|XXX|TBD|TODO|【待|（待|\[待)", re.IGNORECASE)
LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)")
TIME_RE = re.compile(r"(\d{4}[-/年]\d{1,2}|\d{1,2}月|Q[1-4]|第[一二三四]季度|季度|周内|月内)")


def read_text(path):
    """按 UTF-8(-SIG) 读取，失败时回退 GBK。"""
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except FileNotFoundError:
            raise SystemExit("文件不存在：%s" % path)
        except UnicodeDecodeError:
            continue
    raise SystemExit("无法解码文件，请保存为 UTF-8 后重试：%s" % path)


def split_h2(text):
    """把 Markdown 按二级标题切分为 {标题: 正文}。"""
    sections = {}
    current = None
    buf = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf)
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)
    return sections


def find_section(sections, aliases):
    """按关键词匹配二级标题，返回 (标题, 正文) 或 (None, '')。"""
    for title, body in sections.items():
        if any(a in title for a in aliases):
            return title, body
    return None, ""


def split_h3(body):
    """按三级标题切分条目，返回 [(标题, 正文)]。"""
    items = []
    current = None
    buf = []
    for line in body.splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                items.append((current, "\n".join(buf)))
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        items.append((current, "\n".join(buf)))
    return items


def check(text, min_sources, min_tracking, min_policies, min_cases):
    """返回 [(质量门, 是否通过, 说明)]。"""
    results = []
    sections = split_h2(text)

    # 1. 章节结构
    missing = [name for name, aliases in REQUIRED_SECTIONS if not find_section(sections, aliases)[0]]
    results.append(("section_structure", not missing,
                    "缺少章节：" + "、".join(missing) if missing else "四个章节齐全"))

    policy_title, policy_body = find_section(sections, ("政策与标准", "政策"))
    practice_title, practice_body = find_section(sections, ("行业实践",))
    tension_title, tension_body = find_section(sections, ("客户张力", "张力"))
    verdict_title, verdict_body = find_section(sections, ("月度结论", "结论与跟踪"))

    # 2. 传导判断：每条政策都要有“传导”或完整的机制+影响两段
    policy_items = split_h3(policy_body) if policy_body else []
    no_trans = [
        t for t, b in policy_items
        if not ("传导" in b or ("作用机制" in b and "对客户影响" in b))
    ]
    count_ok = min_policies <= len(policy_items) <= 5
    if not policy_items:
        results.append(("transmission_judgment_present", False, "未识别到政策条目（需三级标题 ###）"))
    else:
        ok = not no_trans and count_ok
        detail = "政策条目 %d 条" % len(policy_items)
        if not count_ok:
            detail += "（应为 %d-5 条）" % min_policies
        if no_trans:
            detail += "；缺传导判断：" + "、".join(no_trans)
        results.append(("transmission_judgment_present", ok, detail))

    # 3. 案例可拆解：问题/做法/成果/可复制条件
    case_items = split_h3(practice_body) if practice_body else []
    bad_cases = []
    for title, body in case_items:
        need = {
            "问题": "问题" in body or "问题" in title,
            "做法": any(k in body for k in ("做法", "措施", "行动")),
            "成果": any(k in body for k in ("成果", "效果", "成效", "结果")),
            "可复制": any(k in body for k in ("可复制", "适用条件", "适用性")),
        }
        if not all(need.values()):
            bad_cases.append("%s（缺：%s）" % (title, "、".join(k for k, v in need.items() if not v)))
    cases_ok = len(case_items) >= min_cases and not bad_cases
    case_detail = "案例 %d 个" % len(case_items)
    if len(case_items) < min_cases:
        case_detail += "（应至少 %d 个）" % min_cases
    if bad_cases:
        case_detail += "；" + "；".join(bad_cases)
    results.append(("peer_cases_decomposable", cases_ok, case_detail))

    # 4. 张力：有证据，或显式声明无张力
    tb = tension_body or ""
    if "张力" not in tb and "张力" not in (tension_title or ""):
        results.append(("tension_identified_or_explicitly_cleared", False, "未提及张力"))
    elif "未识别到显著张力" in tb or "未发现显著张力" in tb:
        has_basis = any(k in tb for k in ("依据", "检索", "证据"))
        results.append(("tension_identified_or_explicitly_cleared", has_basis,
                        "已声明无张力" + ("，含检索依据" if has_basis else "，但缺少判断依据")))
    else:
        has_evidence = any(k in tb for k in ("证据", "依据")) or bool(LINK_RE.search(tb))
        results.append(("tension_identified_or_explicitly_cleared", has_evidence,
                        "已识别张力" + ("" if has_evidence else "，但缺少证据/来源")))

    # 5. 对标：至少一张对比表或“对标”结论
    has_table = "|" in (tension_body or "") and "---" in (tension_body or "")
    has_bench = "对标" in (tension_body or "") or "对标" in (tension_title or "")
    results.append(("peer_comparison_present", has_table and has_bench,
                    "包含对标表" if has_table and has_bench else "缺少同行对标表或对标结论"))

    # 6. 跟踪点：>=3 个，且每个含时间与验证方式
    #    忽略“数据缺口/声明/附录”等收尾小节中的列表项
    track_scope = verdict_body or ""
    cut = re.search(r"^###\s+.*(数据缺口|声明|附录)", track_scope, re.MULTILINE)
    if cut:
        track_scope = track_scope[:cut.start()]
    track_lines = [
        ln.strip() for ln in track_scope.splitlines()
        if re.match(r"^\s*[-*]\s+\S", ln)
    ]
    bad_tracks = [ln for ln in track_lines if not (TIME_RE.search(ln) and "验证" in ln)]
    tracks_ok = len(track_lines) >= min_tracking and not bad_tracks
    track_detail = "跟踪点 %d 个" % len(track_lines)
    if len(track_lines) < min_tracking:
        track_detail += "（应至少 %d 个）" % min_tracking
    if bad_tracks:
        track_detail += "；%d 个缺时间或验证方式" % len(bad_tracks)
    results.append(("tracking_points_falsifiable", tracks_ok, track_detail))

    # 7. 来源：链接数与“来源”标注（兼容 **来源**： 的写法）
    links = LINK_RE.findall(text)
    source_marks = len(re.findall(r"来源[*_]{0,4}\s*[:：]", text))
    src_ok = len(links) >= min_sources and source_marks >= 3
    results.append(("sources_cited", src_ok,
                    "链接 %d 条，来源标注 %d 处（链接应 >= %d，标注 >= 3）"
                    % (len(links), source_marks, min_sources)))

    # 8. 占位符 / 无源数字启发式
    placeholders = PLACEHOLDER_RE.findall(text)
    fab_ok = not placeholders
    results.append(("no_fabricated_data", fab_ok,
                    "发现占位符：" + "、".join(sorted(set(placeholders))) if placeholders
                    else "无占位符（真实性仍需人工核验）"))

    return results


def main():
    # Windows 控制台默认 GBK，统一改为 UTF-8，避免中文乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="ESG 月报结构与质量门校验")
    parser.add_argument("brief", help="月报 Markdown 文件路径")
    parser.add_argument("--min-sources", type=int, default=5, help="最少可核验来源链接数")
    parser.add_argument("--min-tracking", type=int, default=3, help="最少跟踪点数")
    parser.add_argument("--min-policies", type=int, default=3, help="最少政策条目数")
    parser.add_argument("--min-cases", type=int, default=2, help="最少案例数")
    args = parser.parse_args()

    text = read_text(args.brief)
    results = check(text, args.min_sources, args.min_tracking, args.min_policies, args.min_cases)

    print("=" * 60)
    print("ESG 月报校验：%s" % args.brief)
    print("=" * 60)
    for name, ok, detail in results:
        print("[%s] %-46s %s" % ("PASS" if ok else "FAIL", name, detail))

    failed = [r for r in results if not r[1]]
    print("-" * 60)
    if failed:
        print("结果：%d 项未通过，请按说明修改后重跑。" % len(failed))
        sys.exit(1)
    print("结果：全部通过。提示：脚本无法校验数据真实性，请人工回溯来源。")


if __name__ == "__main__":
    main()
