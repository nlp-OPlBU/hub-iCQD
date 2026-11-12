"""DeepSeek 配置、真实 SDK 请求协议和连续追问测试；不访问网络、不消耗 API 额度。"""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI

from model_config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, ModelSettings, get_model_settings
from sql_agent import DEFAULT_DB, PROMPTS, DemoBackend, OpenAICompatibleBackend, ReadOnlyDatabase, build_graph, initial_state
from web_app import create_app


SECRET = 'unit-test-key-never-send-to-network'


def settings(key=SECRET):
    return ModelSettings('deepseek', key, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, 'live')


def completion(reply, finish_reason='stop'):
    text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
    return httpx.Response(200, json={
        'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 0,
        'model': DEEPSEEK_MODEL,
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text},
                     'finish_reason': finish_reason}],
    })


@contextmanager
def sdk_backend(handler):
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        with OpenAI(api_key=SECRET, base_url=DEEPSEEK_BASE_URL,
                    http_client=http_client, max_retries=0) as client:
            yield OpenAICompatibleBackend(settings(), client=client)


class DeepSeekTests(unittest.TestCase):
    def test_default_configuration_is_deepseek_live_without_key(self):
        result = get_model_settings(env_path=Path('/nonexistent-test-path/.env'), environ={})
        self.assertEqual(result.provider, 'deepseek')
        self.assertEqual(result.base_url, 'https://api.deepseek.com')
        self.assertEqual(result.model, 'deepseek-flash')
        self.assertEqual(result.mode, 'live')
        self.assertFalse(result.ready)

    def test_fill_only_api_key_is_sufficient(self):
        result = get_model_settings(env_path=Path('/nonexistent-test-path/.env'),
                                    environ={'DEEPSEEK_API_KEY': SECRET})
        self.assertTrue(result.ready)
        self.assertEqual(result.model, DEEPSEEK_MODEL)

    def test_file_configuration_and_environment_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('DEEPSEEK_API_KEY=file-placeholder\nDEEPSEEK_MODEL=file-model\n')
            result = get_model_settings(env, {'DEEPSEEK_API_KEY': SECRET, 'DEEPSEEK_MODEL': 'env-model'})
            self.assertEqual(result.api_key, SECRET)
            self.assertEqual(result.model, 'env-model')
        self.assertNotIn(SECRET, repr(result))

    def test_other_provider_key_is_not_used_for_deepseek(self):
        result = get_model_settings(Path('/nonexistent-test-path/.env'), {'OPENAI_API_KEY': SECRET})
        self.assertFalse(result.ready)
        self.assertEqual(result.api_key, '')

    def test_placeholders_are_not_treated_as_real_keys(self):
        self.assertFalse(settings('your-deepseek-api-key').ready)

    def test_explicit_demo_is_available(self):
        client = TestClient(create_app(mode='demo'))
        self.assertTrue(client.get('/api/config').json()['ready'])
        response = client.post('/api/chat', json={'question': '一共有多少位客户？'})
        result = json.loads(response.text.splitlines()[-1])['result']
        self.assertEqual(result['results'], [{'customer_count': 59}])

    def test_missing_key_does_not_fall_back_to_demo(self):
        with patch('web_app.get_model_settings', return_value=settings('')):
            client = TestClient(create_app())
        config = client.get('/api/config').json()
        self.assertEqual(config['mode'], 'live')
        self.assertEqual(config['provider_name'], 'DeepSeek')
        self.assertEqual(config['model'], DEEPSEEK_MODEL)
        self.assertFalse(config['ready'])
        self.assertEqual(client.post('/api/chat', json={'question': '任意数据问题'}).status_code, 503)

    def test_config_response_never_contains_key(self):
        with patch('web_app.get_model_settings', return_value=settings()):
            client = TestClient(create_app())
        response = client.get('/api/config')
        self.assertNotIn(SECRET, response.text)
        self.assertTrue(response.json()['ready'])

    def test_native_sdk_sends_deepseek_json_mode_and_history(self):
        captured = []

        def handler(request):
            self.assertEqual(request.url.host, 'api.deepseek.com')
            self.assertEqual(request.url.path, '/chat/completions')
            body = json.loads(request.content)
            captured.append(body)
            return completion({'question': '美国消费金额最高的前5位客户'})

        history = [{'role': 'user', 'content': '美国消费金额最高的前3位客户'},
                   {'role': 'assistant', 'content': '上次SQL：WHERE Country = USA LIMIT 3'}]
        with sdk_backend(handler) as backend:
            reply = backend.complete('resolve_question', {'question': '那前五位呢？', 'history': history})
        self.assertIn('前5位', reply['question'])
        body = captured[0]
        self.assertEqual(body['model'], DEEPSEEK_MODEL)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['thinking'], {'type': 'disabled'})
        self.assertEqual(body['max_tokens'], 4096)
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(body['messages'][1:3], history)
        self.assertNotIn('history', json.loads(body['messages'][-1]['content']))

    def test_blank_json_response_retries_once(self):
        calls = []

        def handler(request):
            calls.append(request)
            return completion('   ' if len(calls) == 1 else {'tables': ['customers']})

        with sdk_backend(handler) as backend:
            self.assertEqual(backend.complete('select_tables', {'question': '客户数'}), {'tables': ['customers']})
        self.assertEqual(len(calls), 2)

    def test_permanently_blank_response_is_bounded(self):
        calls = []

        def handler(request):
            calls.append(request)
            return completion('   ')

        with sdk_backend(handler) as backend:
            with self.assertRaisesRegex(ValueError, '空白'):
                backend.complete('select_tables', {'question': '客户数'})
        self.assertEqual(len(calls), 2)

    def test_truncated_json_response_is_not_executed(self):
        with sdk_backend(lambda _: completion('{"sql": "SELECT', 'length')) as backend:
            with self.assertRaisesRegex(ValueError, '长度上限'):
                backend.complete('generate_sql', {'question': '客户数'})

    def test_authentication_and_balance_errors_do_not_leak_key(self):
        for status, expected in [(401, '鉴权失败'), (402, '余额不足'), (429, '请求过于频繁')]:
            with self.subTest(status=status):
                with sdk_backend(lambda _: httpx.Response(status, json={
                    'error': {'message': SECRET, 'type': 'test_error'}})) as backend:
                    with self.assertRaises(RuntimeError) as error:
                        backend.complete('select_tables', {'question': '客户数'})
                self.assertIn(expected, str(error.exception))
                self.assertNotIn(SECRET, str(error.exception))

    def test_two_turn_query_uses_real_sdk_and_preserves_filter(self):
        calls = []
        first_question = '消费金额最高的前 3 位美国客户是谁？'
        standalone_followup = '消费金额最高的前 5 位美国客户是谁？'

        def handler(request):
            body = json.loads(request.content)
            stage = next(key for key, prompt in PROMPTS.items() if prompt == body['messages'][0]['content'])
            payload = json.loads(body['messages'][-1]['content'])
            calls.append((stage, body, payload))
            if stage == 'resolve_question':
                self.assertIn('USA', body['messages'][2]['content'])
                reply = {'question': standalone_followup}
            elif stage == 'select_tables':
                reply = {'tables': ['customers', 'invoices']}
            elif stage == 'generate_sql':
                limit = 5 if '5' in payload['question'] else 3
                reply = {'sql': "SELECT c.CustomerId, c.FirstName, c.Country, ROUND(SUM(i.Total),2) AS total "
                                "FROM customers c JOIN invoices i ON c.CustomerId=i.CustomerId "
                                "WHERE c.Country='USA' GROUP BY c.CustomerId "
                                f"ORDER BY total DESC, c.CustomerId LIMIT {limit};"}
            elif stage == 'summarize':
                reply = {'summary': f"查询得到 {len(payload['results'])} 位美国客户。"}
            else:
                self.fail('不应进入其他模型阶段')
            return completion(reply)

        with sdk_backend(handler) as backend:
            with patch('web_app.get_model_settings', return_value=settings()):
                client = TestClient(create_app(mode='live', backend_factory=lambda _: backend))
            first = client.post('/api/chat', json={'question': first_question})
            result1 = json.loads(first.text.splitlines()[-1])['result']
            self.assertEqual(result1['status'], 'success')
            self.assertEqual(len(result1['results']), 3)
            history = [{'role': 'user', 'content': first_question},
                       {'role': 'assistant', 'content': result1['summary'] + '\nSQL: ' + result1['sql']}]
            second = client.post('/api/chat', json={'question': '那前五位呢？', 'history': history})
            result2 = json.loads(second.text.splitlines()[-1])['result']
        self.assertEqual(result2['status'], 'success')
        self.assertEqual(result2['question'], '那前五位呢？')
        self.assertEqual(result2['resolved_question'], standalone_followup)
        self.assertIn('resolve_question', result2['trace'])
        self.assertEqual(len(result2['results']), 5)
        self.assertTrue(all(row['Country'] == 'USA' for row in result2['results']))
        self.assertIn("c.Country='USA'", result2['sql'])
        self.assertEqual(len(calls), 7)  # 首轮三个模型节点；追问多一个指代消解节点
        for _, body, _ in calls[3:]:
            self.assertEqual(body['messages'][1:3], history)

    def test_ambiguous_followup_asks_for_clarification_without_query(self):
        class AmbiguousBackend(DemoBackend):
            def complete(self, stage, payload):
                if stage == 'resolve_question':
                    return {'clarification': '你指的是哪个国家的客户？'}
                return super().complete(stage, payload)

        state = build_graph(ReadOnlyDatabase(DEFAULT_DB), AmbiguousBackend()).invoke(
            initial_state('那里有多少人？', [{'role': 'user', 'content': '查询美国和加拿大客户'}]))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error_kind'], 'clarification')
        self.assertIn('需要补充说明', state['summary'])
        self.assertNotIn('execute_sql', state['trace'])


if __name__ == '__main__':
    unittest.main()
