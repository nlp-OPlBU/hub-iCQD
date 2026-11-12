"""Present saved real stdout as a readable evidence page; does not simulate a terminal."""
from pathlib import Path
from datetime import datetime
import html
import json

root=Path(__file__).resolve().parent.parent
docs=root/'docs'
image=json.loads((docs/'practice-image.json').read_text())
redis=(docs/'redis-container-run.txt').read_text()
kafka=(docs/'kafka-container-run.txt').read_text()
tests=(docs/'automated-test-log.txt').read_text()
summary=next((x for x in tests.splitlines() if ' passed' in x),'see full test log')
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第十四周 · Docker 镜像实践真实记录</title><style>
body{font:15px/1.6 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;background:#f1f4ef;color:#20372c;margin:0;padding:36px}main{max-width:1100px;margin:auto}h1{font-size:30px;margin:6px 0 8px}header{border-bottom:1px solid #ccd8cd;padding-bottom:20px}.eyebrow{font-size:12px;letter-spacing:2px;color:#5b7061}header p{font-size:13px;color:#5b7061}.meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.meta span{background:white;border:1px solid #d0d9d0;padding:6px 12px;border-radius:6px;font-size:12px}section{background:white;border:1px solid #d0d9d0;border-radius:12px;margin:18px 0;padding:20px 24px}h2{font-size:19px;margin:0 0 12px}.command{font:11px/1.8 ui-monospace,monospace;color:#466c53;background:#f2f6ef;padding:10px 13px;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere}pre{font:12px/1.75 ui-monospace,'PingFang SC',monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:15px 0 0;padding:16px;background:#f7f8f5;color:#243d2c;border-radius:6px}.foot{font-size:11px;color:#5b7061;overflow-wrap:anywhere}.result{color:#246446;font-weight:600}@media(max-width:700px){body{padding:16px}h1{font-size:23px}section{padding:15px}pre{font-size:11px}}
</style><main><header><div class="eyebrow">COURSEWORK 14 / EXECUTION EVIDENCE</div><h1>Docker 镜像中的 Redis 与 Kafka 实践</h1><p>王鑫炜 · 本页读取本次实际 Docker 运行保存的 stdout。页面是日志回放，不是终端模拟器，也不表示云端运行。</p><div class="meta"><span>镜像：wxw-week14-practice:local</span><span>Redis 7.4 · Apache Kafka 4.0.1</span><span class="result">独立容器连接真实中间件</span></div></header>'''
sections=[('01 / Redis：历史对话写入与读回',
 'docker run --rm --network wxw-week14_default -e REDIS_URL=redis://redis:6379/0 wxw-week14-practice:local python redis_demo.py',redis),
 ('02 / Kafka：生产、消费与手动提交 offset',
 'docker run --rm --network wxw-week14_default -e KAFKA_BOOTSTRAP_SERVERS=kafka:29092 wxw-week14-practice:local python kafka_demo.py',kafka)]
for title,command,output in sections:
 page+='<section><h2>'+html.escape(title)+'</h2><div class="command">'+html.escape(command)+'</div><pre>'+html.escape(output)+'</pre></section>'
page+='<section><h2>03 / 自动化验证</h2><div class="result">'+html.escape(summary)+'</div><p class="foot">基础用例、真实 Redis/Kafka 集成；模型单元用例使用明确标注的 Mock，真实模型证据另存 vision-preflight.json 和问答记录。</p></section>'
page+='<p class="foot">实际镜像 ID：'+html.escape(image['image_id'])+'<br>镜像大小：'+str(image['size_bytes'])+' bytes · 记录生成时间：'+datetime.now().astimezone().isoformat(timespec='seconds')+'</p></main></html>'
(docs/'镜像实践记录.html').write_text(page)
print('Generated evidence page from actual saved logs')
