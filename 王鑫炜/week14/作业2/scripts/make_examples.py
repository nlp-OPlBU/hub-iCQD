"""Create original, shareable coursework fixtures (not execution evidence)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'examples'
OUT.mkdir(exist_ok=True)
font_path = '/System/Library/Fonts/Supplemental/Arial.ttf'
if not Path(font_path).exists():
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
font = ImageFont.truetype(font_path, 26)
small = ImageFont.truetype(font_path, 19)
im = Image.new('RGB', (1400, 540), '#f5f7f2')
d = ImageDraw.Draw(im)
boxes = [(30,180,260,310,'PDF upload'),(320,180,550,310,'Kafka task'),(610,180,840,310,'MinerU worker'),(900,180,1130,310,'Text + figures')]
for x1,y1,x2,y2,label in boxes:
    d.rounded_rectangle((x1,y1,x2,y2),14,fill='#e3ecdf',outline='#356349',width=3)
    d.text(((x1+x2)/2,(y1+y2)/2),label,anchor='mm',font=font,fill='#20432f')
for end,start in [(260,320),(550,610),(840,900)]:
    d.line((end+5,245,start-8,245), fill='#356349', width=4)
    d.polygon([(start-8,245),(start-20,236),(start-20,254)],fill='#356349')
d.text((38,65),'ASYNCHRONOUS DOCUMENT PIPELINE',font=ImageFont.truetype(font_path,34),fill='#20432f')
d.rounded_rectangle((330,370,660,460),12,fill='#e8edf2',outline='#547291',width=2)
d.text((495,415),'Redis: chat history',anchor='mm',font=font,fill='#324a66')
d.rounded_rectangle((880,370,1340,460),12,fill='#e8edf2',outline='#547291',width=2)
d.text((1110,415),'Retrieve + vision LLM',anchor='mm',font=font,fill='#324a66')
d.line((1015,315,1015,363),fill='#547291',width=3)
d.polygon([(1015,367),(1006,355),(1024,355)],fill='#547291')
d.line((668,415,872,415),fill='#547291',width=3)
d.polygon([(877,415),(865,406),(865,424)],fill='#547291')
d.text((35,497),'States: queued -> processing -> completed / failed',font=small,fill='#526355')
im.save(OUT / 'architecture.png')
question = Image.new('RGB', (1000, 420), '#ffffff')
qd = ImageDraw.Draw(question)
qd.text((45,35),'Which component keeps the upload request fast?',font=font,fill='#20362a')
for i,label in enumerate(['Web upload','Kafka queue','MinerU worker']):
    x=40+i*325
    qd.rounded_rectangle((x,155,x+270,270),12,fill='#e3ecdf',outline='#356349',width=3)
    qd.text((x+135,212),label,font=font,anchor='mm',fill='#20362a')
    if i<2:
        qd.text((x+285,210),'->',font=font,fill='#20362a')
qd.text((45,332),'Question: Why does the browser not wait for parsing?',font=small,fill='#526355')
question.save(OUT / 'question.png')
pdfmetrics.registerFont(TTFont('STSong-Light', '/System/Library/Fonts/Supplemental/Songti.ttc', subfontIndex=0))

def write_pdf(path, title, paragraphs, diagram=False):
    c=canvas.Canvas(str(path),pagesize=A4)
    width,height=A4
    c.setFillColorRGB(.13,.26,.19);c.setFont('STSong-Light',24)
    c.drawString(45,height-65,title)
    c.setFont('STSong-Light',11);c.setFillColorRGB(.36,.42,.37)
    c.drawString(45,height-90,'王鑫炜 / 第十四周原创演示资料 / 2026-11-12')
    y=height-128
    for heading,body in paragraphs:
        c.setFillColorRGB(.13,.26,.19);c.setFont('STSong-Light',13);c.drawString(45,y,heading);y-=23
        c.setFont('STSong-Light',11);c.setFillColorRGB(.18,.22,.19)
        # Fixed lines are sized by font width, avoiding ambiguous CJK wrapping.
        line=''
        for ch in body:
            if pdfmetrics.stringWidth(line+ch,'STSong-Light',11)>width-90:
                c.drawString(45,y,line);y-=19;line=''
            line+=ch
        if line:c.drawString(45,y,line);y-=19
        y-=18
    if diagram:
        c.drawImage(ImageReader(OUT/'architecture.png'),45,100,width=width-90,height=(width-90)*540/1400)
        c.setFont('STSong-Light',10);c.drawString(45,80,'图1：Kafka 异步文档解析与 Redis 对话历史的数据路径。')
    c.setFont('STSong-Light',9);c.setFillColorRGB(.36,.42,.37)
    c.drawString(45,35,'示例数据：用于功能验证，不代表真实业务统计。');c.drawRightString(width-45,35,'1 / 1')
    c.save()

write_pdf(OUT/'多模态RAG架构说明.pdf','多模态 RAG：从文档到答案',[
 ('一、上传与异步解析','浏览器一次可上传多份 PDF。后端先保存原文件，并在 SQLite 的同一事务中写入文档元数据和 outbox。接口返回 202，不等待 MinerU 完成。Dispatcher 在 Kafka 确认后将 outbox 标为已发送。'),
 ('二、解析状态与可靠消费','文档最初为待解析（queued）。Kafka Worker 领取任务后写为解析中（processing），调用真实 MinerU，存储文本片段和图片后写为解析完成（completed），最后提交消费 offset。失败时显示 failed 并允许重试。'),
 ('三、多模态问答','检索文档文本和图片说明，关联同页插图。视觉大模型同时接收用户问题、用户附图、检索片段和文档插图；返回经过校验的文字块与图片块，前端按顺序图文混排。回答标注文档名称和页码。'),
 ('四、对话历史','Redis List 保存用户与助手消息，使用 RPUSH 追加，LRANGE 读取最近历史，LTRIM 限制长度，EXPIRE 设置有效期。追问时从 Redis 取历史，先还原问题，再检索和生成。')],True)
write_pdf(OUT/'Redis与Kafka实践笔记.pdf','Redis 与 Kafka：分工与边界',[
 ('一、Redis 存储历史对话','Redis 是内存数据存储，适合低延迟读取和追加会话历史。本项目用 List 保存按时间排序的消息，键按会话隔离，设置七天 TTL，最多保留一百条消息。AOF 用于持久化。TTL 到期后历史会被移除。'),
 ('二、Kafka 传递解析任务','Kafka 是持久化事件流平台。Producer 发送文档 ID、事件 ID、重试代次等元数据。PDF 字节保存在共享持久卷，消息只传引用，避免大消息。Worker 的 Consumer 使用同一个 group，按分区顺序处理并手动提交 offset。'),
 ('三、为什么需要幂等','Kafka 消费成功后到提交 offset 前发生崩溃，会造成重复投递。本作业采用至少一次处理、文档 ID 和 attempt 校验及 Redis 互斥锁，保证同一代任务不会重复写入解析结果。不能把这一机制宣称为端到端 exactly-once。'),
 ('四、Outbox 的作用','如果先写数据库再直接发送消息，进程崩溃可能丢失任务；如果先发消息再写数据库，Worker 可能看到不存在的文档。事务 outbox 将元数据与待发送事件一起落库，后台重试发送；重复投递由 Worker 幂等处理。'),
 ('五、适用边界','Redis 保存历史对话并提供锁，Kafka 负责异步任务投递与消费位置。两者职责不同；用 Redis 代替本题的 Kafka，或只在内存数组中模拟消息队列，均不满足这份实践要求。单节点课堂部署也不代表生产级高可用。')])
print('Created 2 original PDF fixtures, architecture.png and question.png')
