"""检索/问答过程中传递的图片对象。"""

from dataclasses import dataclass

from PIL import Image


@dataclass
class UserImage:
    """用户随问题一起上传的图片。"""

    data_url: str          # 直接可以放进 OpenAI 多模态消息的 data URL
    pil: Image.Image       # 用于 CLIP 编码
    filename: str = "image"


@dataclass
class ImageRef:
    """从知识库检索到的图片。"""

    index: int             # 对应提示词里的 [[IMG_n]]
    static_url: str        # /static/processed/... 相对地址
    absolute_url: str      # 浏览器可直接访问的绝对地址
    source_file: str       # 来源 PDF 文件名
    caption: str = ""      # 图片 alt 文本
