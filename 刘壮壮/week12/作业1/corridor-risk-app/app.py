import hashlib
import html
import os
from uuid import uuid4

import streamlit as st

from backend.analyzer import InvalidImageError, ModelServiceError, analyze_image
from backend.risk_rules import RECOMMENDATIONS, build_evidence, classify_risk
from backend.schemas import AnalysisResponse

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

st.set_page_config(
    page_title="楼安巡检｜消防风险识别",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def load_streamlit_secrets() -> None:
    """允许本地 .env 和 Streamlit Secrets 两种服务端配置方式。"""
    try:
        for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
            if key in st.secrets and st.secrets[key]:
                os.environ[key] = str(st.secrets[key])
    except FileNotFoundError:
        pass


def enable_camera() -> None:
    st.session_state.camera_enabled = True


def disable_camera() -> None:
    st.session_state.camera_enabled = False
    st.session_state.pop("camera_capture", None)


def handle_source_change() -> None:
    if st.session_state.image_source != "摄像头拍照":
        disable_camera()


def result_card(result: AnalysisResponse) -> str:
    level_class = {
        "高风险": "risk-high",
        "中等风险": "risk-medium",
        "低风险": "risk-low",
        "无隐患": "risk-none",
        "非楼道": "risk-other",
    }[result.risk_level.value]
    evidence = "".join(f"<li>{html.escape(item)}</li>" for item in result.evidence)
    return f"""
    <section class="result-card {level_class}" aria-label="消防风险分析结果">
      <div class="result-topline">
        <span class="risk-marker" aria-hidden="true"></span>
        <div>
          <span class="result-kicker">综合判定</span>
          <h2>{html.escape(result.risk_level.value)}</h2>
        </div>
        <span class="confidence">置信度 {round(result.confidence * 100)}%</span>
      </div>
      <p class="scene-summary">{html.escape(result.scene_summary)}</p>
      <h3>识别依据</h3>
      <ul class="evidence-list">{evidence}</ul>
      <div class="recommendation">
        <strong>处置建议</strong>
        <p>{html.escape(result.recommendation)}</p>
      </div>
      <span class="request-id">分析编号：{html.escape(result.request_id)}</span>
    </section>
    """


load_streamlit_secrets()

st.markdown(
    """
    <style>
      .stApp {
        background:
          radial-gradient(circle at 8% 0%, rgba(23, 92, 211, .06), transparent 28rem),
          #f6f8fb;
      }
      [data-testid="stHeader"] { background: transparent; }
      [data-testid="stMainBlockContainer"] {
        max-width: 1180px;
        padding-top: 2.5rem;
        padding-bottom: 2rem;
      }
      .brand-row {
        display: flex; align-items: center; gap: .75rem; margin-bottom: 2.6rem;
      }
      .brand-mark {
        width: 2.75rem; height: 2.75rem; display: grid; place-items: center;
        border-radius: .75rem; color: white; background: #b42318;
        font-weight: 800; box-shadow: 0 5px 14px rgba(180, 35, 24, .2);
      }
      .brand-copy strong { display: block; color: #101828; font-size: 1.05rem; }
      .brand-copy span { color: #667085; font-size: .75rem; }
      .eyebrow {
        margin: 0 0 .45rem; color: #175cd3; font-size: .75rem;
        font-weight: 750; letter-spacing: .12em;
      }
      .hero-title {
        max-width: 760px; margin: 0; color: #101828;
        font-size: clamp(2rem, 4vw, 3rem); line-height: 1.15; letter-spacing: -.035em;
      }
      .hero-copy {
        max-width: 720px; margin: .85rem 0 2rem; color: #475467; font-size: 1.05rem;
      }
      [data-testid="stVerticalBlockBorderWrapper"] {
        background: white; border-color: #e4e7ec !important;
        border-radius: 1rem; box-shadow: 0 8px 24px rgba(16, 24, 40, .05);
      }
      .section-label {
        margin: 0; color: #175cd3; font-size: .75rem; font-weight: 750;
        letter-spacing: .05em;
      }
      .section-title { margin: .15rem 0 1rem; color: #101828; font-size: 1.25rem; }
      div[data-testid="stCameraInput"] video { border-radius: .75rem; }
      div[data-testid="stFileUploader"] section {
        min-height: 13rem; border-radius: .75rem; border-color: #cfd4dc;
        background: #fafbfc;
      }
      .stButton > button {
        min-height: 3rem; border-radius: .625rem; font-weight: 700;
        transition: background-color 180ms ease, border-color 180ms ease;
      }
      .result-empty {
        min-height: 24rem; display: grid; place-items: center; text-align: center;
        color: #667085;
      }
      .result-empty strong { display: block; color: #344054; font-size: 1.05rem; }
      .result-empty p { max-width: 280px; margin: .4rem auto 0; font-size: .875rem; }
      .result-card { padding: .25rem .15rem; }
      .result-topline {
        display: grid; grid-template-columns: .75rem 1fr auto; gap: .8rem;
        align-items: center; padding-bottom: 1rem; border-bottom: 1px solid #e4e7ec;
      }
      .risk-marker { width: .75rem; height: 3rem; border-radius: 999px; background: #475467; }
      .risk-high .risk-marker { background: #b42318; }
      .risk-medium .risk-marker { background: #dc6803; }
      .risk-low .risk-marker { background: #f79009; }
      .risk-none .risk-marker { background: #039855; }
      .result-kicker { color: #667085; font-size: .75rem; }
      .result-card h2 { margin: .1rem 0 0; color: #101828; font-size: 1.7rem; }
      .confidence {
        align-self: start; padding: .3rem .5rem; border-radius: .4rem;
        color: #475467; background: #f2f4f7; font-size: .75rem; font-weight: 650;
      }
      .scene-summary { margin: 1.1rem 0; color: #344054; }
      .result-card h3 { margin: 1rem 0 .45rem; color: #101828; font-size: .9rem; }
      .evidence-list { display: grid; gap: .4rem; margin: 0; padding-left: 1.25rem; color: #475467; }
      .evidence-list li { padding-left: .15rem; font-size: .875rem; }
      .recommendation {
        margin-top: 1.25rem; padding: .85rem; color: #1849a9;
        background: #eff4ff; border-radius: .625rem;
      }
      .recommendation strong { font-size: .875rem; }
      .recommendation p { margin: .25rem 0 0; color: #3538cd; font-size: .82rem; }
      .request-id {
        display: block; margin-top: .8rem; color: #98a2b3;
        font-family: ui-monospace, monospace; font-size: .68rem;
      }
      .legend {
        display: grid; grid-template-columns: repeat(4, 1fr); gap: .6rem;
        margin-top: .75rem;
      }
      .legend-item {
        padding: .7rem; border: 1px solid #e4e7ec; border-radius: .65rem;
        color: #667085; background: white; font-size: .75rem;
      }
      .legend-item strong { display: block; margin-bottom: .15rem; color: #344054; }
      .disclaimer {
        max-width: 760px; margin: 1.8rem auto 0; color: #667085;
        text-align: center; font-size: .75rem;
      }
      @media (max-width: 700px) {
        [data-testid="stMainBlockContainer"] { padding: 1.5rem 1rem 2rem; }
        .brand-row { margin-bottom: 1.8rem; }
        .legend { grid-template-columns: 1fr 1fr; }
        .result-topline { grid-template-columns: .65rem 1fr; }
        .confidence { grid-column: 2; justify-self: start; }
      }
      @media (prefers-reduced-motion: reduce) {
        * { scroll-behavior: auto !important; transition-duration: .01ms !important; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="brand-row">
      <span class="brand-mark">安</span>
      <span class="brand-copy"><strong>楼安巡检</strong><span>消防风险辅助识别</span></span>
    </div>
    <p class="eyebrow">CORRIDOR SAFETY INSPECTION</p>
    <h1 class="hero-title">拍摄楼道，快速发现消防隐患</h1>
    <p class="hero-copy">拍照或上传现场图片，系统将根据起火风险、可燃物和通行情况给出分级结果。</p>
    """,
    unsafe_allow_html=True,
)

input_column, result_column = st.columns([1.2, 0.8], gap="large")

with input_column:
    with st.container(border=True):
        st.markdown(
            '<p class="section-label">步骤 1</p><h2 class="section-title">获取现场照片</h2>',
            unsafe_allow_html=True,
        )
        source = st.radio(
            "图片来源",
            ["摄像头拍照", "上传图片"],
            horizontal=True,
            label_visibility="collapsed",
            key="image_source",
            on_change=handle_source_change,
        )
        if source == "摄像头拍照":
            if not st.session_state.get("camera_enabled", False):
                st.info("摄像头尚未启用。点击下方按钮后，浏览器才会请求摄像头权限。")
                st.button(
                    "启用摄像头",
                    on_click=enable_camera,
                    use_container_width=True,
                )
                uploaded_image = None
            else:
                st.button(
                    "关闭摄像头",
                    on_click=disable_camera,
                    use_container_width=True,
                )
                uploaded_image = st.camera_input(
                    "拍摄楼道现场",
                    help="尽量拍摄完整楼道，并包含地面、墙边堆物和通行区域。",
                    key="camera_capture",
                )
        else:
            uploaded_image = st.file_uploader(
                "选择楼道现场图片",
                type=["jpg", "jpeg", "png", "webp"],
                help="支持 JPG、PNG、WebP，文件最大 10 MB。",
            )
            if uploaded_image is not None:
                st.image(uploaded_image, caption="待分析图片", use_container_width=True)

        image_bytes = uploaded_image.getvalue() if uploaded_image is not None else None
        image_hash = hashlib.sha256(image_bytes).hexdigest() if image_bytes else None

        if image_hash != st.session_state.get("image_hash"):
            st.session_state.image_hash = image_hash
            st.session_state.analysis_result = None

        validation_error = None
        if image_bytes and len(image_bytes) > MAX_UPLOAD_BYTES:
            validation_error = "图片不能超过 10 MB，请压缩后重试。"
            st.error(validation_error)
        elif image_bytes:
            st.success("照片已准备好，可以开始分析。")
        else:
            st.caption("请选择一种方式添加清晰、完整的楼道照片。")

        analyze_clicked = st.button(
            "开始风险分析",
            type="primary",
            use_container_width=True,
            disabled=not image_bytes or validation_error is not None,
        )

        if analyze_clicked and image_bytes:
            try:
                with st.spinner("AI 正在识别现场风险，请稍候…"):
                    scene = analyze_image(image_bytes)
                    level = classify_risk(scene)
                    st.session_state.analysis_result = AnalysisResponse(
                        risk_level=level,
                        confidence=round(scene.confidence, 2),
                        scene_summary=scene.scene_summary,
                        evidence=build_evidence(scene, level),
                        recommendation=RECOMMENDATIONS[level],
                        request_id=uuid4().hex[:12],
                    )
            except (InvalidImageError, ModelServiceError) as exc:
                st.session_state.analysis_result = None
                st.error(str(exc))

with result_column:
    with st.container(border=True):
        st.markdown(
            '<p class="section-label">步骤 2</p><h2 class="section-title">识别结果</h2>',
            unsafe_allow_html=True,
        )
        result = st.session_state.get("analysis_result")
        if result is None:
            st.markdown(
                """
                <div class="result-empty">
                  <div><strong>等待分析</strong><p>添加照片后，风险等级和识别依据将在这里展示。</p></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(result_card(result), unsafe_allow_html=True)
            if result.confidence < 0.7:
                st.warning("本次识别置信度较低，建议人工复核或重新拍摄更完整的画面。")

st.markdown(
    """
    <div class="legend" aria-label="风险分级标准">
      <div class="legend-item"><strong>高风险</strong>电动车、电瓶、飞线充电</div>
      <div class="legend-item"><strong>中等风险</strong>严重堵塞或大量可燃物</div>
      <div class="legend-item"><strong>低风险</strong>少量堆物，影响较小</div>
      <div class="legend-item"><strong>无隐患</strong>通道整洁，无堆物</div>
    </div>
    <p class="disclaimer">AI 识别结果仅供日常巡检参考，不能替代消防专业检查；低置信度或现场情况复杂时请人工复核。</p>
    """,
    unsafe_allow_html=True,
)
