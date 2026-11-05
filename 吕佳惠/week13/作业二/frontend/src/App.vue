<template>
  <div class="chat-container">
    <el-card class="chat-card">
      <template #header>
        <div class="header">ChatBI - LangGraph 多Agent SQL Agent</div>
      </template>
      <div class="chat-box" ref="chatBoxRef">
        <div v-for="item in msgList" :key="item.id" class="msg-item">
          <div v-if="item.role === 'user'" class="user-msg">
            <el-tag type="primary">用户</el-tag>
            <div class="msg-text">{{ item.content }}</div>
          </div>
          <div v-else class="bot-msg">
            <el-tag type="success">Agent</el-tag>
            <div class="msg-text">{{ item.answer }}</div>
            <el-collapse v-if="item.sql">
              <el-collapse-item title="查看生成SQL">
                <el-input v-model="item.sql" type="textarea" readonly rows="3" />
              </el-collapse-item>
            </el-collapse>
            <div v-if="item.tableData && item.tableData.length > 0" class="table-wrap">
              <el-table :data="item.tableData" border stripe height="300">
                <el-table-column v-for="k in Object.keys(item.tableData[0])" :key="k" :prop="k" :label="k"/>
              </el-table>
            </div>
          </div>
        </div>
      </div>
      <div class="input-area">
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="2"
          placeholder="输入自然语言查询，例如：统计每个艺术家的专辑数量，按专辑数量降序"
          @keydown.enter.prevent="send"
        />
        <el-button type="primary" :loading="loading" @click="send">发送</el-button>
        <el-button @click="clearAll">清空对话</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { chatQuery } from './api'

const msgList = ref([])
const inputText = ref('')
const loading = ref(false)
const chatBoxRef = ref(null)
let idCounter = 0

const scrollBottom = () => {
  nextTick(() => {
    if (chatBoxRef.value) {
      chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight
    }
  })
}

const send = async () => {
  const q = inputText.value.trim()
  if (!q || loading.value) return
  msgList.value.push({
    id: idCounter++,
    role: 'user',
    content: q
  })
  inputText.value = ''
  loading.value = true
  scrollBottom()
  try {
    const res = await chatQuery(q)
    msgList.value.push({
      id: idCounter++,
      role: 'bot',
      answer: res.data.answer,
      sql: res.data.sql,
      tableData: res.data.table_data
    })
  } catch (err) {
    msgList.value.push({
      id: idCounter++,
      role: 'bot',
      answer: '请求失败，请检查后端是否正常启动！'
    })
  } finally {
    loading.value = false
    scrollBottom()
  }
}

const clearAll = () => {
  msgList.value = []
}
</script>

<style scoped>
.chat-container {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}
.chat-card {
  height: 90vh;
  display: flex;
  flex-direction: column;
}
.header {
  font-size: 18px;
  font-weight: bold;
}
.chat-box {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #fafafa;
}
.msg-item {
  margin-bottom: 18px;
}
.user-msg {
  display: flex;
  gap:8px;
  justify-content: flex-end;
}
.bot-msg {
  display: flex;
  gap:8px;
  flex-direction: column;
  align-items: flex-start;
}
.msg-text {
  margin:4px 0 8px;
  white-space: pre-wrap;
}
.table-wrap {
  margin-top:8px;
  width:100%;
}
.input-area {
  display:flex;
  gap:10px;
  padding:12px;
  border-top:1px solid #eee;
  align-items:flex-end;
}
.input-area .el-textarea {
  flex:1;
}
</style>

