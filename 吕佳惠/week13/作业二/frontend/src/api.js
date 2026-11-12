import axios from 'axios'
const service = axios.create({
  baseURL: 'http://127.0.0.1:8000',
  timeout: 120000
})

export function chatQuery(question) {
  return service.post('/chat', { question })
}
