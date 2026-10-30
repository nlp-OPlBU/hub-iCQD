"""运行：python -m streamlit run frontend.py --server.address 0.0.0.0"""

import hashlib
import os
from io import BytesIO
from urllib.parse import urlsplit

import requests
import streamlit as st
from PIL import Image, UnidentifiedImageError


MAX_SIZE = 10 * 1024 * 1024
IMAGE_MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
CLASSIFICATIONS = {"高风险", "中风险", "低风险", "无风险", "非楼道"}


def validate_url(value: str) -> str:
    """与后端保持一致，只接受不含账号密码的 HTTP/HTTPS 地址。"""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
        parsed.port  # 同时检查端口格式。
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("请输入有效的 HTTP/HTTPS 地址，且不要包含账号密码。")
    return value


def validate_image(content: bytes) -> str:
    """校验实际图片内容，返回上传所需的 MIME 类型。"""
    if not content:
        raise ValueError("图片不能为空。")
    if len(content) > MAX_SIZE:
        raise ValueError("图片大小不能超过 10 MB。")
    try:
        with Image.open(BytesIO(content)) as image:
            mime_type = IMAGE_MIME_TYPES.get(image.format)
            if mime_type is None:
                raise ValueError("仅支持 JPG、PNG、WEBP 图片。")
            image.verify()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("图片无效、已损坏或分辨率过大，请重新选择。") from exc
    return mime_type


def analyze_image(
    backend_url: str,
    *,
    content: bytes | None = None,
    filename: str = "photo.jpg",
    image_url: str | None = None,
) -> dict:
    """按 FastAPI 的 multipart/form-data / 表单约定提交图片。"""
    endpoint = validate_url(backend_url).rstrip("/") + "/analyze_image"
    if (content is None) == (image_url is None):
        raise ValueError("请选择一张图片或提供一个图片 URL。")
    if content is not None:
        kwargs = {"files": {"file": (filename, content, validate_image(content))}}
    else:
        kwargs = {"data": {"image_url": validate_url(image_url)}}

    try:
        with requests.post(endpoint, timeout=(10, 120), **kwargs) as response:
            try:
                payload = response.json()
            except ValueError as exc:
                raise ValueError(
                    f"后端未返回 JSON（HTTP {response.status_code}），请检查后端地址和服务日志。"
                ) from exc
            if not response.ok:
                detail = payload.get("detail", "请查看后端日志。") if isinstance(payload, dict) else "请查看后端日志。"
                raise ValueError(f"分析失败（HTTP {response.status_code}）：{detail}")
    except requests.Timeout as exc:
        raise ValueError("分析请求超时，请稍后重试或检查后端模型服务。") from exc
    except requests.ConnectionError as exc:
        raise ValueError("无法连接后端，请确认服务已启动，并检查侧栏的后端地址。") from exc
    except requests.RequestException as exc:
        raise ValueError("请求失败，请检查后端地址和网络连接。") from exc

    result = payload.get("result") if isinstance(payload, dict) else None
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("classification"), str)
        or result["classification"] not in CLASSIFICATIONS
        or not isinstance(result.get("description"), str)
        or not result["description"].strip()
    ):
        raise ValueError("后端返回的结果格式不正确，需要 result.classification 和 result.description。")
    return result


def render_result(result: dict) -> None:
    classification = result["classification"]
    st.subheader("识别结果")
    if classification == "高风险":
        st.error("风险等级：高风险", icon="🚨")
    elif classification in {"中风险", "低风险"}:
        st.warning(f"风险等级：{classification}", icon="⚠️")
    elif classification == "无风险":
        st.success("风险等级：无风险", icon="✅")
    else:
        st.info("识别结果：非楼道，请提供楼道场景图片。", icon="ℹ️")
    st.markdown("**分析说明**")
    st.write(result["description"])


def main() -> None:
    st.set_page_config(page_title="楼道消防安全识别", page_icon="🧯", layout="centered")
    st.title("🧯 楼道消防安全识别")
    st.caption("拍摄或选择一张楼道照片，识别消防安全隐患及风险等级。")

    with st.sidebar:
        st.header("服务设置")
        backend_url = st.text_input(
            "后端服务地址",
            value=os.getenv("BACKEND_URL", "http://127.0.0.1:8000"),
            help="填写 FastAPI 服务根地址，前端会自动追加 /analyze_image。",
        )
        st.caption("此地址由运行 Streamlit 的电脑访问。")
        with st.expander("风险等级说明"):
            st.markdown(
                "- **高风险**：电动车、电瓶、飞线充电等起火风险。\n"
                "- **中风险**：大量堆积物堵塞通道或大量可燃物。\n"
                "- **低风险**：少量楼道堆物。\n"
                "- **无风险**：楼道整洁，无堆放物。\n"
                "- **非楼道**：图片不是楼道场景。"
            )

    source = st.radio("选择图片来源", ["手机/摄像头拍照", "上传图片", "图片 URL"], horizontal=True)
    content = None
    image_url = None
    filename = "photo.jpg"
    ready = False
    preview = None

    if source == "手机/摄像头拍照":
        st.info("请允许浏览器使用摄像头。手机远程访问时请使用 HTTPS 地址。")
        uploaded = st.camera_input("拍摄楼道照片", key="camera")
    elif source == "上传图片":
        uploaded = st.file_uploader("选择图片", type=["jpg", "jpeg", "png", "webp"], key="upload")
        st.caption("支持 JPG、PNG、WEBP，单张图片不超过 10 MB。")
    else:
        uploaded = None
        raw_url = st.text_input("图片 URL", placeholder="https://example.com/corridor.jpg", key="image_url")
        st.caption("请提供可直接访问的图片地址，后端模型需要能够读取该地址。")
        if raw_url.strip():
            try:
                image_url = validate_url(raw_url)
                preview = image_url
                ready = True
            except ValueError as exc:
                st.error(str(exc))

    if uploaded is not None:
        try:
            if uploaded.size > MAX_SIZE:
                raise ValueError("图片大小不能超过 10 MB。")
            content = uploaded.getvalue()
            validate_image(content)
            filename = uploaded.name
            preview = content
            ready = True
        except ValueError as exc:
            st.error(str(exc))

    if ready:
        st.image(preview, caption="待识别图片", width="stretch")
        if image_url is not None:
            st.caption("若预览未显示，请检查地址是否为图片直链，以及是否有访问限制。")

    # 图片、来源或服务改变后，不再展示上一张图片的结果。
    fingerprint = (source, backend_url.strip(), hashlib.sha256(content).hexdigest() if content else image_url)
    if st.session_state.get("input_fingerprint") != fingerprint:
        st.session_state["input_fingerprint"] = fingerprint
        st.session_state.pop("analysis_result", None)

    if st.button("开始识别", type="primary", disabled=not ready, width="stretch"):
        st.session_state.pop("analysis_result", None)
        try:
            with st.spinner("正在分析图片，请稍候……"):
                st.session_state["analysis_result"] = analyze_image(
                    backend_url, content=content, filename=filename, image_url=image_url
                )
        except ValueError as exc:
            st.error(str(exc))

    if "analysis_result" in st.session_state:
        render_result(st.session_state["analysis_result"])


if __name__ == "__main__":
    main()
