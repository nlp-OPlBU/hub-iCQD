import { createRouter, createWebHashHistory } from 'vue-router'
import DocumentsView from '../views/DocumentsView.vue'
import ChatView from '../views/ChatView.vue'
import DocumentViewer from '../views/DocumentViewer.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/documents' },
    { path: '/documents', component: DocumentsView },
    { path: '/documents/:id/view', component: DocumentViewer },
    { path: '/chat', component: ChatView },
  ],
})
