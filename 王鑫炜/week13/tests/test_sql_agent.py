"""运行：python -m unittest discover -s tests -v。无模型密钥也可验证真实图与 SQLite。"""

import hashlib
import json
import os
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sql_agent import (
    DEFAULT_DB, DEMO_CASES, PROMPTS, DemoBackend, OpenAICompatibleBackend,
    ReadOnlyDatabase, SQLPolicyError,
    build_graph, ensure_readonly_query, initial_state, parse_json_object,
)


class FaultBackend(DemoBackend):
    def __init__(self, initial_sql=None, repeat_error=False, fail_stage=None):
        super().__init__()
        self.initial_sql = initial_sql
        self.repeat_error = repeat_error
        self.fail_stage = fail_stage
        self.calls = []

    def complete(self, stage, payload):
        self.calls.append((stage, payload))
        if stage == self.fail_stage:
            raise ValueError("模拟模型输出格式错误")
        if stage == "generate_sql" and self.initial_sql is not None:
            return {"sql": self.initial_sql}
        if stage == "repair_sql" and self.repeat_error:
            return {"tables": ["invoices"], "sql": self.initial_sql}
        return super().complete(stage, payload)


class SQLAgentTests(unittest.TestCase):
    def setUp(self):
        self.database = ReadOnlyDatabase(DEFAULT_DB)
        self.question = DEMO_CASES[0]["question"]

    def run_agent(self, backend=None, question=None, retries=2, database=None):
        graph = build_graph(database or self.database, backend or DemoBackend(), retries)
        return graph.invoke(initial_state(question or self.question),
                            {"recursion_limit": 20 + 4 * retries})

    def test_database_schema_contains_foreign_keys(self):
        catalog = self.database.catalog()
        self.assertEqual(len(catalog), 11)
        self.assertIn('FOREIGN KEY', catalog['invoices'])
        self.assertIn('CustomerId', catalog['invoices'])

    def test_successful_aggregation(self):
        state = self.run_agent()
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['results'], [{'total_amount': 2328.6}])
        self.assertEqual(state['retry_count'], 0)
        self.assertEqual(state['fails'], [])
        self.assertEqual(state['trace'], ['parse_database', 'select_tables', 'generate_sql',
                                          'validate_sql', 'execute_sql', 'summarize'])

    def test_customer_count(self):
        state = self.run_agent(question=DEMO_CASES[1]['question'])
        self.assertEqual(state['results'], [{'customer_count': 59}])

    def test_join_top_customers(self):
        state = self.run_agent(question=DEMO_CASES[2]['question'])
        self.assertEqual(state['status'], 'success')
        self.assertEqual([r['CustomerId'] for r in state['results']], [6, 26, 57])
        self.assertEqual([r['total_spent'] for r in state['results']], [49.62, 47.62, 46.62])

    def test_invalid_column_repaired_after_validation(self):
        backend = FaultBackend('SELECT MissingColumn FROM invoices;')
        state = self.run_agent(backend)
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['retry_count'], 1)
        self.assertEqual(len(state['fails']), 1)
        self.assertIsNone(state['error'])
        self.assertEqual(state['trace'].count('execute_sql'), 1)
        repair_payload = next(p for stage, p in backend.calls if stage == 'repair_sql')
        for field in ['question', 'sql', 'schema', 'catalog', 'error', 'fails', 'retry_count']:
            self.assertIn(field, repair_payload)

    def test_runtime_error_also_enters_repair_loop(self):
        # EXPLAIN 可通过，但实际求 abs 时会发生整数溢出。
        sql = 'SELECT abs(-9223372036854775808) AS amount;'
        self.database.validate(sql)
        state = self.run_agent(FaultBackend(sql))
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['retry_count'], 1)
        self.assertEqual(state['trace'].count('execute_sql'), 2)
        self.assertIn('execute_sql: integer overflow', state['fails'][0])

    def test_retry_budget_stops_loop(self):
        state = self.run_agent(FaultBackend('SELECT MissingColumn FROM invoices;', repeat_error=True))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['retry_count'], 2)
        self.assertEqual(len(state['fails']), 3)  # 初次错误 + 两次修复后仍出错
        self.assertIsNone(state['results'])
        self.assertNotIn('summarize', state['trace'])
        self.assertEqual(state['trace'][-1], 'handle_failure')

    def test_zero_retry_budget(self):
        state = self.run_agent(DemoBackend(inject_bad_sql=True), retries=0)
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['retry_count'], 0)
        self.assertNotIn('repair_sql', state['trace'])

    def test_empty_results_are_success(self):
        state = self.run_agent(question=DEMO_CASES[3]['question'])
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['results'], [])
        self.assertEqual(state['retry_count'], 0)
        self.assertIn('未查到', state['summary'])

    def test_old_failures_do_not_trigger_more_repairs(self):
        state = self.run_agent(DemoBackend(inject_bad_sql=True))
        self.assertEqual(state['status'], 'success')
        self.assertTrue(state['fails'])
        self.assertIsNone(state['error'])
        self.assertEqual(state['trace'].count('repair_sql'), 1)

    def test_result_truncation_is_explicit(self):
        state = self.run_agent(question=DEMO_CASES[2]['question'],
                               database=ReadOnlyDatabase(DEFAULT_DB, max_rows=2))
        self.assertEqual(len(state['results']), 2)
        self.assertTrue(state['truncated'])
        self.assertIn('预览', state['summary'])

    def test_blocked_statement_is_not_repaired(self):
        before = hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest()
        state = self.run_agent(FaultBackend('DELETE FROM invoices;'))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error_kind'], 'blocked')
        self.assertEqual(state['retry_count'], 0)
        self.assertNotIn('execute_sql', state['trace'])
        self.assertEqual(before, hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest())

    def test_readonly_sql_policy(self):
        statements = [
            'DELETE FROM invoices;', 'UPDATE invoices SET Total=0;',
            'DROP TABLE invoices;', 'PRAGMA table_info(invoices);',
            "ATTACH DATABASE '/tmp/other.db' AS other;",
            'SELECT 1; DELETE FROM invoices;',
            'WITH x AS (SELECT 1) DELETE FROM invoices;',
        ]
        for sql in statements:
            with self.subTest(sql=sql), self.assertRaises(SQLPolicyError):
                ensure_readonly_query(sql)

    def test_cte_and_union_are_supported(self):
        sql = 'WITH x AS (SELECT COUNT(*) AS n FROM customers) SELECT n FROM x;'
        self.database.validate(sql)
        rows, truncated = self.database.execute(sql)
        self.assertEqual(rows, [{'n': 59}])
        self.assertFalse(truncated)
        rows, _ = self.database.execute('SELECT 1 AS n UNION SELECT 2 AS n;')
        self.assertEqual(rows, [{'n': 1}, {'n': 2}])

    def test_extension_loading_is_denied(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self.database.validate("SELECT load_extension('not-a-library');")

    def test_duplicate_column_names_are_not_silently_lost(self):
        state = self.run_agent(FaultBackend('SELECT 1 AS x, 2 AS x;'))
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['retry_count'], 1)
        self.assertIn('同名列', state['fails'][0])

    def test_infrastructure_error_does_not_rewrite_sql(self):
        class LockedDatabase(ReadOnlyDatabase):
            def execute(self, sql):
                raise sqlite3.OperationalError('database is locked')
        state = self.run_agent(database=LockedDatabase(DEFAULT_DB))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error_kind'], 'database')
        self.assertEqual(state['retry_count'], 0)

    def test_expensive_query_is_interrupted(self):
        database = ReadOnlyDatabase(DEFAULT_DB, query_timeout=0.01)
        sql = ('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n '
               'WHERE x<100000000) SELECT SUM(x) AS total FROM n;')
        with self.assertRaisesRegex(sqlite3.OperationalError, 'interrupted'):
            database.execute(sql)

    def test_model_failure_exits_cleanly(self):
        state = self.run_agent(FaultBackend(fail_stage='generate_sql'))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error_kind'], 'model')
        self.assertNotIn('execute_sql', state['trace'])

    def test_summary_failure_keeps_successful_query(self):
        state = self.run_agent(FaultBackend(fail_stage='summarize'))
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['results'], [{'total_amount': 2328.6}])
        self.assertEqual(state['retry_count'], 0)
        self.assertIn('自动汇总失败', state['summary'])

    def test_unknown_table_is_rejected(self):
        class UnknownTableBackend(DemoBackend):
            def complete(self, stage, payload):
                if stage == 'select_tables':
                    return {'tables': ['imaginary_table']}
                return super().complete(stage, payload)
        state = self.run_agent(UnknownTableBackend())
        self.assertEqual(state['status'], 'failed')
        self.assertNotIn('generate_sql', state['trace'])

    def test_invalid_repair_output_consumes_attempt_and_stops(self):
        class BadRepairBackend(DemoBackend):
            def complete(self, stage, payload):
                if stage == 'repair_sql':
                    return {'sql': 'SELECT 1;'}  # 缺少 tables
                return super().complete(stage, payload)
        state = self.run_agent(BadRepairBackend(inject_bad_sql=True))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['retry_count'], 1)

    def test_real_backend_wiring_with_mocked_sdk(self):
        # 经过真实 Backend 的 SDK 请求构造和 JSON 解析，但不发网络请求。
        demo = DemoBackend()
        client = MagicMock()

        def fake_completion(**kwargs):
            system, user = kwargs['messages']
            stage = next(key for key, prompt in PROMPTS.items() if prompt == system['content'])
            payload = json.loads(user['content'])
            response = demo.complete(stage, payload)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(response)))])

        client.chat.completions.create.side_effect = fake_completion
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'unit-test-placeholder',
                                     'SQL_AGENT_PROVIDER': 'openai',
                                     'OPENAI_MODEL': 'unit-test-model',
                                     'OPENAI_BASE_URL': 'https://example.invalid/v1'}):
            with patch('openai.OpenAI', return_value=client) as constructor:
                state = self.run_agent(OpenAICompatibleBackend())
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['results'], [{'total_amount': 2328.6}])
        self.assertEqual(client.chat.completions.create.call_count, 3)
        self.assertEqual(constructor.call_args.kwargs['base_url'], 'https://example.invalid/v1')

    def test_json_parsing_and_input_validation(self):
        self.assertEqual(parse_json_object('```json\n{"sql":"SELECT 1"}\n```'),
                         {'sql': 'SELECT 1'})
        with self.assertRaises(ValueError):
            parse_json_object('[]')
        with self.assertRaises(json.JSONDecodeError):
            parse_json_object('not json')
        with self.assertRaises(ValueError):
            initial_state('   ')
        with self.assertRaises(ValueError):
            build_graph(self.database, DemoBackend(), -1)
        with self.assertRaises(FileNotFoundError):
            ReadOnlyDatabase(DEFAULT_DB.parent / 'missing.db')


if __name__ == '__main__':
    unittest.main()
