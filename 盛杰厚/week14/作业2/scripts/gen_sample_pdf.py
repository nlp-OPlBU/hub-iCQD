"""生成示例 PDF（供没有现成文档时演示 / 自测）。

    conda activate py312
    python scripts/gen_sample_pdf.py            # -> samples/demo_multimodal_rag.pdf
    python scripts/gen_sample_pdf.py --pages 6  # 更长

内容刻意覆盖了功能 3 需要的三种块：
* 文本段落（含明确的事实句，方便提问验证召回）
* 表格（营收/成本对比）
* 图片 3 张（架构图、趋势折线图、对比柱状图），并且正文里**明确引用**了图号，
  这样「图文混排输出」能被验证：问架构图怎么设计的，应该把架构图插进答案里。

中文字体：优先 Songti.ttc / Arial Unicode.ttf（reportlab 可直接嵌入），
都找不到时退回英文，保证脚本在任何机器上都能跑。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "samples"

CJK_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
]


def pick_cjk_font():
    """返回 (reportlab 字体名, matplotlib FontProperties 或 None)。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path, idx in CJK_CANDIDATES:
        p = Path(path)
        if not p.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("CJKSample", str(p), subfontIndex=idx))
        except Exception:
            continue
        props = None
        try:
            from matplotlib import font_manager

            font_manager.fontManager.addfont(str(p))
            name = font_manager.FontProperties(fname=str(p)).get_name()
            props = font_manager.FontProperties(family=name)
        except Exception:
            props = None
        return "CJKSample", props
    return "Helvetica", None


# ---------------------------------------------------------------------------
# 三张配图
# ---------------------------------------------------------------------------
def make_figures(out_dir: Path, cjk_props) -> list:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    if cjk_props is not None:
        plt.rcParams["font.family"] = cjk_props.get_name()
    plt.rcParams["axes.unicode_minus"] = False
    figs_dir = out_dir / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    # ---- 图 1：系统架构（Kafka 三段式流水线）----
    fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=150)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    ax.axis("off")
    boxes = [
        (0.3, 3.2, "浏览器\n上传 PDF"),
        (2.5, 3.2, "Web 后端\nFastAPI"),
        (4.7, 3.2, "Kafka\nmmrag.pdf.upload"),
        (7.0, 3.2, "解析 Worker\nMinerU"),
        (0.3, 1.0, "向量库\nFAISS + SQLite"),
        (2.6, 1.0, "入库 Worker\nbge 向量化"),
        (5.0, 1.0, "图片描述\nqwen-vl"),
        (7.4, 1.0, "问答 Worker\n百炼生成"),
    ]
    for x, y, label in boxes:
        ax.add_patch(FancyBboxPatch((x, y), 2.0, 1.1, boxstyle="round,pad=0.06",
                                    fc="#e8f0fe", ec="#3b6fd4", lw=1.2))
        ax.text(x + 1.0, y + 0.55, label, ha="center", va="center", fontsize=8.5)
    arrows = [((2.3, 3.75), (2.5, 3.75)), ((4.5, 3.75), (4.7, 3.75)),
              ((6.7, 3.75), (7.0, 3.75)), ((8.0, 3.2), (8.0, 2.4)),
              ((2.5, 1.55), (0.3 + 2.0, 1.55)), ((4.7, 1.55), (2.6 + 2.0, 1.55)),
              ((7.1, 1.55), (5.0 + 2.0, 1.55))]
    for (x0, y0), (x1, y1) in arrows:
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=12,
                                     color="#555", lw=1.1))
    ax.text(5, 4.6, "图 1  多模态 RAG 系统架构：上传经 Kafka 异步投递到解析与入库 Worker",
            ha="center", fontsize=9.5)
    p = figs_dir / "fig1_arch.png"
    fig.tight_layout()
    fig.savefig(p)
    plt.close(fig)
    paths.append(p)

    # ---- 图 2：解析耗时趋势 ----
    fig, ax = plt.subplots(figsize=(6.4, 2.8), dpi=150)
    pages = [1, 2, 4, 6, 8, 10, 14, 20]
    mineru = [1.8, 3.1, 6.4, 9.2, 12.5, 15.1, 21.8, 30.6]
    builtin = [0.4, 0.7, 1.2, 1.7, 2.3, 2.9, 4.1, 5.8]
    ax.plot(pages, mineru, marker="o", label="MinerU (pipeline)", color="#d9534f")
    ax.plot(pages, builtin, marker="s", label="内置解析器", color="#3b6fd4")
    ax.set_xlabel("PDF 页数")
    ax.set_ylabel("解析耗时 (秒)")
    ax.set_title("图 2  两种解析方式的耗时随页数变化")
    ax.grid(alpha=0.3)
    ax.legend()
    p = figs_dir / "fig2_latency.png"
    fig.tight_layout()
    fig.savefig(p)
    plt.close(fig)
    paths.append(p)

    # ---- 图 3：召回率对比柱状图 ----
    fig, ax = plt.subplots(figsize=(6.4, 2.8), dpi=150)
    groups = ["纯文本", "文本+表格", "文本+表格+图片描述"]
    recall = [0.62, 0.74, 0.88]
    bars = ax.bar(groups, recall, color=["#9db8e8", "#5b8ade", "#2f5fb3"], width=0.55)
    for b, v in zip(bars, recall):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}", ha="center", fontsize=9)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Top-5 召回率")
    ax.set_title("图 3  图片描述参与向量检索后，多模态召回率提升")
    p = figs_dir / "fig3_recall.png"
    fig.tight_layout()
    fig.savefig(p)
    plt.close(fig)
    paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# PDF 正文
# ---------------------------------------------------------------------------
TEXT_CN = {
    "title": "多模态知识库问答系统：设计与评测",
    "abs_h": "摘要",
    "abs": "本文介绍一套基于 Kafka 与 MinerU 的多模态检索增强生成（RAG）系统。用户上传 PDF 后，"
           "系统通过 Kafka 把解析任务异步投递给 Worker，Worker 调用 MinerU 还原版式、抽取文本、表格与图片，"
           "再使用 bge-small-zh-v1.5 把内容向量化写入 FAISS 索引，最终由阿里百炼的大模型完成图文混排的回答。",
    "s1_h": "1. 系统架构",
    "s1": "如图 1 所示，整个链路被拆成四个进程角色：Web 后端、解析 Worker、入库 Worker 与问答 Worker，"
          "它们之间只通过 Kafka topic 通信。选择 Kafka 的原因是解析单个 PDF 需要数秒到数十秒，"
          "同步 HTTP 会长时间占用连接并导致超时；改成消息队列后，上传接口只需写入一条任务消息即可返回，"
          "解析进度再由状态 topic 以 SSE 推送回浏览器。同一文档的任务使用 doc_id 作为消息 key，"
          "保证落到同一分区、被同一消费者按序处理，避免状态机出现「解析完成」早于「解析中」的乱序。",
    "s1b": "topic 设计为四条：mmrag.pdf.upload（待解析任务）、mmrag.pdf.parsed（解析产物交接）、"
           "mmrag.doc.status（解析状态：待解析、解析中、解析完成）以及 mmrag.qa.request 与 mmrag.qa.answer"
           "（多模态问答的请求与流式回答）。大体积的解析结果不放进消息体，而是落盘后只传目录路径，"
           "以免触发 broker 的 message.max.bytes 限制。",
    "s2_h": "2. 文档解析与切块",
    "s2": "解析层优先使用 MinerU，它在版面还原、表格结构和公式识别上明显优于普通文本抽取库；"
          "当 MinerU 服务不可用时，系统退回内置解析器（pdfplumber 读取文本行与图片框，"
          "pypdfium2 把图片框所在区域渲染成 PNG）。两者产出同一种中间结构，下游完全无感。"
          "切块策略是：文本块按句子边界打包到 600 字并保留 80 字重叠，表格块整块保留，"
          "图片块一块一图。文本块会把所属标题拼进待嵌入向量以补充上下文。",
    "s3_h": "3. 让图片可以被检索到",
    "s3": "向量模型 bge-small-zh-v1.5 只接受文本输入，因此图片本身无法直接进入向量空间。"
          "系统的做法是给每张图补一段「文字化」的表示：优先使用解析器识别出的题注（例如“图 3 …”），"
          "题注缺失时调用视觉模型 qwen-vl 生成一句描述，再把「[图片] 第 N 页 + 描述」这一整句向量化，"
          "与文本块放进同一个 FAISS 索引。这样当用户问「系统架构图长什么样」时，"
          "图片块能与文本块一起被召回，检索命中后再把图片以 /api/images/{id} 的形式交给模型和前端，"
          "答案就能做到真正的图文混排。图 3 给出了这一策略带来的召回率变化。",
    "s4_h": "4. 多模态问答",
    "s4": "提问支持两种输入：纯文字，以及「图 + 问」。当用户上传自己的截图时，系统先用视觉模型读图，"
          "把描述拼进检索 query，再把原始图片与检索到的文档配图放进同一个 user 消息发送给付费模型，"
          "因此模型既看到用户的问题，也看到图片本身。回答以流式方式产出，边生成边推送到前端。",
    "s5_h": "5. 评测结果",
    "s5": "我们在一份 20 页的内部文档上做了对比实验，数据见表 1。总体来看，加入图片描述后 Top-5 召回率"
          "从 0.74 提升到 0.88，代价是首次解析多花约 30% 的时间；解析耗时如图 2 所示，"
          "内置解析器在 10 页以内的文档上快 5 倍以上，适合纯文字文档的快速入库。",
    "table_h": "表 1  不同配置下的检索与解析指标",
    "concl_h": "6. 结论",
    "concl": "把解析与入库放进 Kafka 消费者，让系统在文档量大、解析慢的场景下依然能保持前端响应；"
             "把图片文字化后与文本共用一个向量空间，是用轻量文本向量模型实现多模态检索的务实做法。"
             "后续工作是把文本向量模型换成真正的多模态 embedding，直接对图片做向量检索。",
}

TABLE_ROWS = [
    ["配置", "Top-5 召回率", "首字延迟", "解析耗时(20页)"],
    ["纯文本块", "0.62", "1.9s", "21.8s"],
    ["文本 + 表格块", "0.74", "2.1s", "22.4s"],
    ["文本 + 表格 + 图片描述", "0.88", "2.4s", "29.1s"],
    ["仅 BM25 关键词", "0.48", "0.3s", "21.8s"],
]


def build_pdf(out_path: Path, font_name: str, figures: list, pages: int = 4) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    body = ParagraphStyle("body", fontName=font_name, fontSize=10.5, leading=17,
                          spaceAfter=8, firstLineIndent=21)
    head = ParagraphStyle("head", fontName=font_name, fontSize=13.5, leading=20,
                          spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#1a3f7a"))
    title = ParagraphStyle("title", fontName=font_name, fontSize=19, leading=26,
                           alignment=1, spaceAfter=14)
    cap = ParagraphStyle("cap", fontName=font_name, fontSize=9, leading=13, alignment=1,
                         textColor=colors.HexColor("#555555"), spaceAfter=10)

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=18 * mm,
                            rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=TEXT_CN["title"], author="week14 作业2 示例文档")
    story = [
        Paragraph(TEXT_CN["title"], title),
        Paragraph(TEXT_CN["abs_h"], head), Paragraph(TEXT_CN["abs"], body),
        Paragraph(TEXT_CN["s1_h"], head), Paragraph(TEXT_CN["s1"], body),
        Paragraph(TEXT_CN["s1b"], body),
    ]
    if figures:
        story += [Spacer(1, 4 * mm), Image(str(figures[0]), width=155 * mm, height=73 * mm),
                  Paragraph("图 1  多模态 RAG 系统架构（Kafka 解耦四个进程角色）", cap)]
    story += [Paragraph(TEXT_CN["s2_h"], head), Paragraph(TEXT_CN["s2"], body),
              Paragraph(TEXT_CN["s3_h"], head), Paragraph(TEXT_CN["s3"], body)]
    if len(figures) > 2:
        story += [Spacer(1, 4 * mm), Image(str(figures[2]), width=125 * mm, height=55 * mm),
                  Paragraph("图 3  图片描述参与检索后的召回率对比", cap)]
    story += [Paragraph(TEXT_CN["s4_h"], head), Paragraph(TEXT_CN["s4"], body),
              Paragraph(TEXT_CN["s5_h"], head), Paragraph(TEXT_CN["s5"], body),
              Paragraph(TEXT_CN["table_h"], cap)]

    tbl = Table(TABLE_ROWS, colWidths=[52 * mm, 32 * mm, 28 * mm, 38 * mm])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe6f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9fb3cd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fb")]),
    ]))
    story += [tbl, Spacer(1, 6 * mm)]
    if len(figures) > 1:
        story += [Image(str(figures[1]), width=130 * mm, height=57 * mm),
                  Paragraph("图 2  MinerU 与内置解析器的耗时对比", cap)]
    story += [Paragraph(TEXT_CN["concl_h"], head), Paragraph(TEXT_CN["concl"], body)]

    # 追加更多页：重复关键事实，便于测试跨页召回
    extra = [
        ("附录 A. 关键参数", "bge-small-zh-v1.5 输出 512 维向量，查询侧需要加指令前缀"
                        "「为这个句子生成表示以用于检索相关文章：」。FAISS 使用内积索引 "
                        "IndexFlatIP，配合 L2 归一化等价于余弦相似度。相似度阈值取 0.30。"),
        ("附录 B. 状态机", "文档状态只有四种：待解析（已上传、任务已进 Kafka）、"
                       "解析中（Worker 已取到任务，细分 MinerU 解析、抽取图片、生成图片描述、向量化、"
                       "写入索引五个阶段）、解析完成（可被检索）、解析失败（附错误信息）。"
                       "状态变化统一发到 mmrag.doc.status，前端以 SSE 订阅。"),
        ("附录 C. 故障与降级", "Kafka 不可用时可降级为进程内队列以便调试，但状态接口会如实标注当前后端；"
                          "MinerU 不可用时退回内置解析器；未配置百炼 Key 时问答走 mock 模式，"
                          "回答由检索片段机械拼装，仍带图片 markdown 以便验证渲染。"),
    ]
    for i in range(max(0, pages - 4)):
        h, t = extra[i % len(extra)]
        story += [Paragraph(f"{h}（第 {i + 2} 次）", head), Paragraph(t, body)]

    doc.build(story)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成演示用示例 PDF")
    ap.add_argument("--pages", type=int, default=4, help="大致页数（>=4）")
    ap.add_argument("--out", default="", help="输出路径，默认 samples/demo_multimodal_rag.pdf")
    args = ap.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    font_name, props = pick_cjk_font()
    print(f"中文字体: {font_name}" + ("" if font_name == "CJKSample" else "（未找到 CJK 字体，正文可能显示为方块）"))
    figures = make_figures(OUT_DIR, props)
    out = Path(args.out) if args.out else OUT_DIR / "demo_multimodal_rag.pdf"
    build_pdf(out, font_name, figures, pages=max(4, args.pages))
    size = out.stat().st_size
    print(f"已生成 {out}（{size / 1024:.0f} KB，配图 {len(figures)} 张于 {OUT_DIR / 'figures'}）")
    print("下一步：把该文件在网页上上传，或直接跑  python scripts/smoke.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
