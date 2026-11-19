

from typing import Any

import requests
import streamlit as st

# 后端实现 -> 默认地址
BACKENDS = {
    "LangGraph 后端（8001）": "http://127.0.0.1:8001",
    "OpenAI Agents 后端（8002）": "http://127.0.0.1:8002",
}

SENTIMENT_STYLE = {
    "positive": "😊 正向",
    "negative": "😔 负面",
    "neutral": "😐 中性",
}
STRATEGY_STYLE = {
    "encourage": "鼓励",
    "comfort": "排解",
    "guide": "引导",
}

st.set_page_config(
    page_title="情感陪伴助手",
    page_icon="💬",
    layout="centered",
)

# ---------------------------------------------------------------------------
# 会话状态
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []  # [{"role": "user"/"assistant", "content": str, "meta": dict|None}]
if "backend_name" not in st.session_state:
    st.session_state.backend_name = next(iter(BACKENDS))
if "health" not in st.session_state:
    st.session_state.health = None


def check_health(url: str) -> dict[str, Any]:
    """调用后端的 /health，确认服务状态。"""
    response = requests.get(f"{url.rstrip('/')}/health", timeout=5)
    response.raise_for_status()
    return response.json()


def call_backend(url: str, message: str) -> dict[str, Any]:
    """把用户输入发给后端，返回情感识别结果与回复。"""
    response = requests.post(
        f"{url.rstrip('/')}/chat",
        json={"message": message},
        timeout=120,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"后端返回 {response.status_code}：{detail}")
    return response.json()


def render_meta(meta: dict[str, Any]) -> None:
    """展示情感识别结果、使用的策略和后端名称。"""
    sentiment = meta.get("sentiment", {})
    label = SENTIMENT_STYLE.get(sentiment.get("label"), sentiment.get("label", "未知"))
    strategy = STRATEGY_STYLE.get(meta.get("strategy"), meta.get("strategy", "未知"))
    confidence = float(sentiment.get("confidence") or 0)
    st.caption(
        f"{label} · 情绪：{sentiment.get('emotion', '未知')} · "
        f"置信度 {confidence:.0%} ｜ 策略：{strategy} ｜ "
        f"后端：{meta.get('backend', '未知')} ｜ 模型：{meta.get('model', '未知')}"
    )


# ---------------------------------------------------------------------------
# 侧边栏：后端选择与状态
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 后端设置")
    backend_name = st.selectbox(
        "选择后端实现",
        list(BACKENDS),
        index=list(BACKENDS).index(st.session_state.backend_name),
    )
    if st.session_state.backend_name != backend_name:
        st.session_state.backend_name = backend_name
        st.session_state.health = None  # 切换后端后，清掉上一次的连接检测结果

    default_url = BACKENDS[backend_name]
    url_key = f"url::{backend_name}"
    if url_key not in st.session_state:
        st.session_state[url_key] = default_url
    backend_url = st.text_input("后端地址", key=url_key)

    if st.button("检测连接"):
        try:
            info = check_health(backend_url)
            st.session_state.health = {"ok": True, "info": info}
        except requests.exceptions.ConnectionError:
            st.session_state.health = {
                "ok": False,
                "error": f"无法连接 {backend_url}，请确认后端已启动。",
            }
        except Exception as exc:
            st.session_state.health = {"ok": False, "error": f"检测失败：{exc}"}

    health = st.session_state.health
    if health:
        if health["ok"]:
            info = health["info"]
            st.success(
                f"服务正常：{info.get('backend')}，模型 {info.get('model')}"
                + ("" if info.get("configured") else "（尚未配置 API Key）")
            )
        else:
            st.error(health["error"])

    if st.button("清空对话"):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.caption("首次使用请先启动一个后端，再在下方输入。")
    st.code(
        "python backend_langgraph.py\n"
        "python backend_openai_agents.py\n"
        "streamlit run frontend.py",
        language="bash",
    )

# ---------------------------------------------------------------------------
# 主体：对话
# ---------------------------------------------------------------------------
st.title("💬 情感陪伴助手")
st.caption("先识别情绪，再给出回应：正向时鼓励你，负面时帮你排解。")

for item in st.session_state.messages:
    role = item["role"]
    with st.chat_message(role, avatar="🧑" if role == "user" else "🤖"):
        st.markdown(item["content"])
        if item.get("meta"):
            render_meta(item["meta"])

prompt = st.chat_input("说说你现在的心情…")
if prompt:
    prompt = prompt.strip()
    if not prompt:
        st.warning("请输入内容后再发送。")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt, "meta": None})
        with st.chat_message("user", avatar="🧑"):
            st.markdown(prompt)

        with st.chat_message("assistant", avatar="🤖"):
            try:
                with st.spinner("正在识别情感并生成回复…"):
                    data = call_backend(backend_url, prompt)
            except requests.exceptions.ConnectionError:
                st.error(f"无法连接后端 {backend_url}，请确认后端已启动。")
            except requests.exceptions.Timeout:
                st.error("请求超时，请稍后重试。")
            except Exception as exc:
                st.error(f"调用失败：{exc}")
            else:
                st.markdown(data["reply"])
                render_meta(data)
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": data["reply"],
                        "meta": data,
                    }
                )
