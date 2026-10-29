import base64
import os

from flask import Flask, jsonify, render_template, request

from vision_client import analyze_risk

app = Flask(__name__)

# 允许直接透传给多模态模型的图片 MIME 类型
_ALLOWED_MIME = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}


def _decode_data_url(data_url: str):
    if not data_url or "," not in data_url:
        raise ValueError("无效的图片数据")
    head, b64 = data_url.split(",", 1)
    mime = "image/jpeg"
    if head.startswith("data:") and ";" in head:
        mime = head[len("data:") :].split(";", 1)[0]
    image_bytes = base64.b64decode(b64)
    return image_bytes, mime


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    try:
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            data_url = payload.get("image") or payload.get("image_url") or ""
            image_bytes, mime = _decode_data_url(data_url)
        else:
            f = request.files.get("image") or request.files.get("file")
            if f is None:
                return jsonify({"error": "未收到图片，请上传文件或提供 image 字段"}), 400
            image_bytes = f.read()
            mime = _ALLOWED_MIME.get(f.mimetype, "image/jpeg")

        if not image_bytes:
            return jsonify({"error": "图片内容为空"}), 400

        provider = request.values.get("provider") or None
        result = analyze_risk(image_bytes, mime=mime, provider=provider)
        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:  # noqa: BLE001 - 兜底，避免把堆栈暴露给前端
        return jsonify({"error": f"服务器内部错误：{e}"}), 500


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    use_ssl = os.environ.get("FLASK_SSL", "0") == "1"
    kwargs = {
        "host": host,
        "port": port,
        # 关闭 watchdog 热重载，避免 Windows 下误报“文件变更”导致请求中断
        "use_reloader": False,
    }
        app.run(ssl_context="adhoc", **kwargs)
    else:
        app.run(**kwargs)
