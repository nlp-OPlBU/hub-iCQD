import os
import sqlite3
import pandas as pd
from sqlalchemy import create_engine, inspect, text
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

# ========== 配置（通义千问 qwen3.8-max） ==========
os.environ["OPENAI_API_KEY"] = "sk-"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen3.8-max"
DB_PATH = "chinook.db"

# 连接数据库
engine = create_engine(f"sqlite:///{DB_PATH}")

llm = ChatOpenAI(
    model=MODEL_NAME,
    temperature=0,
    reasoning_effort="none"
)

# ----------------------
# 步骤1：数据库解析 DBParser
# ----------------------
class DBParser:
    def __init__(self, engine):
        self.engine = engine
        self.inspector = inspect(engine)
        self.tables = self.inspector.get_table_names()
        self.table_info = {}
        self._parse_all_tables()

    def _parse_all_tables(self):
        for tbl in self.tables:
            cols = self.inspector.get_columns(tbl)
            col_desc = []
            for c in cols:
                col_desc.append(f"{c['name']} ({c['type']})")
            self.table_info[tbl] = ", ".join(col_desc)

    def get_all_tables(self):
        return self.tables

    def get_table_schema(self, table_list):
        res = ""
        for t in table_list:
            res += f"【表 {t}】字段：{self.table_info[t]}\n"
        return res

print("===== 步骤1：数据库解析 =====")
parser = DBParser(engine)
all_tables = parser.get_all_tables()
print("全部表：", all_tables)

# ----------------------
# 步骤2：表选择 Agent
# ----------------------
table_select_prompt = ChatPromptTemplate.from_messages([
    ("system", """你是数据库专家。根据用户问题，从给定表列表里选出相关表，只输出json：{{"tables": ["表名1","表名2"]}}
表列表：{all_tables}
"""),
    ("human", "用户问题：{user_query}")
])
table_selector = table_select_prompt | llm | JsonOutputParser()

# ----------------------
# 步骤3：SQL生成 Agent
# ----------------------
sql_gen_prompt = ChatPromptTemplate.from_messages([
    ("system", """根据用户问题 + 表结构，生成可直接在sqlite运行的SQL。只输出JSON：{{"sql":"生成的sql"}}
禁止select *；字段写清楚；注意表关联。
表结构：{schema_info}
"""),
    ("human", "用户问题：{user_query}")
])
sql_generator = sql_gen_prompt | llm | JsonOutputParser()

# ----------------------
# 步骤4 & 5：SQL校验 + SQL改写
# ----------------------
sql_check_prompt = ChatPromptTemplate.from_messages([
    ("system", """检查SQL是否存在语法错误、逻辑错误、注入风险。
输出json：{{"is_valid":true/false,"fixed_sql":"修正后的sql","remark":"说明"}}
sqlite语法。
表结构：{schema_info}
"""),
    ("human", "待校验SQL：{raw_sql}")
])
sql_checker = sql_check_prompt | llm | JsonOutputParser()

# ----------------------
# 步骤6：SQL执行
# ----------------------
def run_sql(engine, sql):
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn)
        return {"ok": True, "data": df}
    except Exception as e:
        return {"ok": False, "err": str(e)}

# ----------------------
# 步骤7：结果汇总（自然语言回答）
# ----------------------
summary_prompt = ChatPromptTemplate.from_messages([
    ("system", "根据SQL查询结果，用中文自然语言回答用户问题，简洁清晰。"),
    ("human", "用户问题：{user_query}\n查询结果：{df_content}")
])
summary_chain = summary_prompt | llm

# ======================
# 整体Workflow入口
# ======================
def sql_agent_workflow(user_query: str):
    print("\n===== 步骤2：表选择 =====")
    select_out = table_selector.invoke({
        "all_tables": all_tables,
        "user_query": user_query
    })
    selected_tables = select_out["tables"]
    print("选中表：", selected_tables)

    schema_text = parser.get_table_schema(selected_tables)
    print("\n===== 步骤3：SQL生成 =====")
    gen_out = sql_generator.invoke({
        "schema_info": schema_text,
        "user_query": user_query
    })
    raw_sql = gen_out["sql"]
    print("原始SQL：", raw_sql)

    print("\n===== 步骤4、5：SQL校验与改写 =====")
    check_out = sql_checker.invoke({
        "schema_info": schema_text,
        "raw_sql": raw_sql
    })
    fixed_sql = check_out["fixed_sql"]
    print("校验结果：", check_out)

    print("\n===== 步骤6：SQL执行 =====")
    exec_res = run_sql(engine, fixed_sql)
    if not exec_res["ok"]:
        return f"SQL执行失败：{exec_res['err']}"
    df = exec_res["data"]
    print("查询结果：")
    print(df)

    print("\n===== 步骤7：结果汇总 =====")
    summary = summary_chain.invoke({
        "user_query": user_query,
        "df_content": df.to_markdown(index=False)
    })
    return summary.content


if __name__ == "__main__":
    question = "找出销量最高的前5首歌曲，显示歌曲名称和总销量"
    ans = sql_agent_workflow(question)
    print("\n===== 最终回答 =====")
    print(ans)