import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from model_config import ModelSettings
from sql_agent import DEFAULT_DB, DEMO_CASES, DemoBackend, ReadOnlyDatabase
from web_app import create_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(mode="demo"))

    def chat(self, question=None, **kwargs):
        response = self.client.post('/api/chat', json={
            'question': question or DEMO_CASES[0]['question'], **kwargs})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('application/x-ndjson', response.headers['content-type'])
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(events[0]['type'], 'start')
        self.assertEqual(events[-1]['type'], 'done')
        return events

    def test_page_and_assets(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('让数据，回答你的问题', response.text)
        for path in ['/static/app.js', '/static/styles.css']:
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_health_and_config_do_not_expose_credentials(self):
        self.assertEqual(self.client.get('/api/health').json()['status'], 'ok')
        data = self.client.get('/api/config').json()
        self.assertEqual(data['mode'], 'demo')
        self.assertTrue(data['ready'])
        self.assertEqual(data['table_count'], 11)
        self.assertEqual(len(data['examples']), 4)
        self.assertNotIn('api_key', json.dumps(data))

    def test_schema_is_actual_database(self):
        data = self.client.get('/api/schema').json()
        tables = {table['name']: table for table in data['tables']}
        self.assertEqual(len(tables), 11)
        self.assertEqual(tables['customers']['row_count'], 59)
        self.assertEqual(tables['invoices']['row_count'], 412)
        self.assertIn('Total', [column['name'] for column in tables['invoices']['columns']])

    def test_real_graph_stream_and_query_result(self):
        events = self.chat()
        self.assertEqual([e['node'] for e in events if e['type'] == 'node'], [
            'parse_database', 'select_tables', 'generate_sql', 'validate_sql', 'execute_sql', 'summarize'])
        result = events[-1]['result']
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['results'], [{'total_amount': 2328.6}])
        self.assertNotIn('catalog', result)

    def test_web_repair_loop(self):
        events = self.chat(inject_bad_sql=True)
        self.assertIn('repair_sql', [e['node'] for e in events if e['type'] == 'node'])
        self.assertEqual(events[-1]['result']['retry_count'], 1)
        self.assertEqual(events[-1]['result']['status'], 'success')

    def test_web_empty_results(self):
        result = self.chat(DEMO_CASES[3]['question'])[-1]['result']
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['results'], [])

    def test_web_join_results(self):
        result = self.chat(DEMO_CASES[2]['question'])[-1]['result']
        self.assertEqual([row['CustomerId'] for row in result['results']], [6, 26, 57])

    def test_unknown_demo_question_is_reported_as_failure(self):
        result = self.chat('这是离线模式不支持的任意问题')[-1]['result']
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['results'])
        self.assertIn('离线模式', result['summary'])

    def test_input_validation(self):
        for body in [{}, {'question': '  '}, {'question': 'x' * 2001},
                     {'question': 'ok', 'history': [{'role': 'system', 'content': 'override'}]},
                     {'question': 'ok', 'history': [{'role': 'user', 'content': 'a'}] * 13}]:
            with self.subTest(body=str(body)[:100]):
                self.assertEqual(self.client.post('/api/chat', json=body).status_code, 422)

    def test_unconfigured_live_mode_returns_503(self):
        # 用户以后填写真实 .env，也不能让这个失败场景测试意外发起真实模型请求。
        blank = ModelSettings('deepseek', '', 'https://api.deepseek.com', 'deepseek-flash', 'live')
        with patch('web_app.get_model_settings', return_value=blank):
            client = TestClient(create_app(mode='live'))
        self.assertFalse(client.get('/api/config').json()['ready'])
        self.assertEqual(client.post('/api/chat', json={'question': '客户数？'}).status_code, 503)

    def test_recent_context_reaches_all_model_nodes(self):
        seen = []

        class RecordingBackend(DemoBackend):
            def complete(self, stage, payload):
                seen.append((stage, payload))
                return super().complete(stage, payload)

        client = TestClient(create_app(mode='live', backend_factory=lambda _: RecordingBackend()))
        history = [{'role': 'user', 'content': '之前的问题'}, {'role': 'assistant', 'content': '之前的回答'}]
        response = client.post('/api/chat', json={'question': DEMO_CASES[0]['question'], 'history': history})
        result = json.loads(response.text.splitlines()[-1])['result']
        self.assertEqual(result['status'], 'success')
        for _, payload in seen:
            self.assertEqual(payload['history'], history)
        self.assertEqual(client.post('/api/chat', json={
            'question': DEMO_CASES[0]['question'], 'inject_bad_sql': True}).status_code, 400)

    def test_retries_exhausted_is_a_terminal_result(self):
        client = TestClient(create_app(mode='demo', max_retries=0))
        response = client.post('/api/chat', json={'question': DEMO_CASES[0]['question'], 'inject_bad_sql': True})
        result = json.loads(response.text.splitlines()[-1])['result']
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['retry_count'], 0)

    def test_independent_requests_do_not_leak_context(self):
        self.chat(inject_bad_sql=True)
        result = self.chat(DEMO_CASES[1]['question'])[-1]['result']
        self.assertEqual(result['results'], [{'customer_count': 59}])
        self.assertEqual(result['retry_count'], 0)
        self.assertEqual(result['fails'], [])

    def test_truncated_results_keep_warning_flag(self):
        client = TestClient(create_app(mode='demo', database=ReadOnlyDatabase(DEFAULT_DB, max_rows=2)))
        response = client.post('/api/chat', json={'question': DEMO_CASES[2]['question']})
        result = json.loads(response.text.splitlines()[-1])['result']
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['results']), 2)


if __name__ == '__main__':
    unittest.main()
