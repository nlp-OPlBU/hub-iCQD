import os
import sqlite3
import pandas as pd
from typing import TypedDict
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END, START

# ========== 模型配置 阿里云百炼 ==========
os.environ["OPENAI_API_KEY"] = "sk-"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
llm = ChatOpenAI(model="qwen-flash", temperature=0)
DB_PATH = "chinook.db"

# ========== 全局状态 State ==========
class AgentState(TypedDict):
    question: str
    all_tables: list[str]
    selected_tables: str
    table_schemas: str
    sql: str
    is_valid: bool
    error_msg: str
    query_df: pd.DataFrame
    final_answer: str
    retry_times: int

# ------------------------------
# 数据库解析（读取所有表）
# ------------------------------
def db_parser(state: AgentState) -> AgentState:
    print("\n🔧【数据库解析节点】读取全部表")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [t[0] for t in cur.fetchall()]
    conn.close()
    print(f"数据库所有表：{tables}")
    return {**state, "all_tables": tables}

# ------------------------------
# 子Agent1：表选择 Agent
# ------------------------------
def table_selector_agent(state: AgentState) -> AgentState:
    print("\n🤖【表选择Agent】执行")
    sys_prompt = SystemMessage(content="""
你是数据库表选择专家。根据用户查询需求，从提供的表列表里选出需要用到的表。
只输出表名，逗号分隔，不要额外解释。
""")
    user_prompt = HumanMessage(content=f"""
可用表列表：{state["all_tables"]}
用户问题：{state["question"]}
""")
    resp = llm.invoke([sys_prompt, user_prompt])
    selected = resp.content.strip()
    print(f"选中表：{selected}")
    return {**state, "selected_tables": selected}

# ------------------------------
# 子Agent2：Schema读取Agent
# ------------------------------
def schema_reader_agent(state: AgentState) -> AgentState:
    print("\n🤖【Schema读取Agent】执行")
    conn = sqlite3.connect(DB_PATH)
    schema_text = ""
    for t in state["selected_tables"].split(","):
        t = t.strip()
        if not t:
            continue
        info_df = pd.read_sql(f"PRAGMA table_info({t})", conn)
        schema_text += f"\n==== 表 {t} ====\n{info_df.to_string()}\n"
    conn.close()
    print("读取表结构完成")
    return {**state, "table_schemas": schema_text}

# ------------------------------
# 子Agent3：SQL生成Agent（负责初次生成 + 出错改写）
# ------------------------------
def sql_generator_agent(state: AgentState) -> AgentState:
    print("\n🤖【SQL生成Agent】执行")
    if state["error_msg"]:
        # 有错误：改写模式
        sys_prompt = SystemMessage(content="""
你是SQLite专家，修正报错的SQL语句。
只允许SELECT查询，禁止DELETE / UPDATE / DROP / ALTER等修改语句。
仅输出SQL，不要markdown、不要解释。
""")
        user_prompt = HumanMessage(content=f"""
表结构：{state["table_schemas"]}
用户问题：{state["question"]}
旧SQL：{state["sql"]}
报错信息：{state["error_msg"]}
""")
    else:
        # 初次生成
        sys_prompt = SystemMessage(content="""
你是SQLite专家，根据表结构生成查询SQL。
只允许SELECT查询，禁止修改类语句。仅输出SQL，不要markdown、不要解释。
""")
        user_prompt = HumanMessage(content=f"""
表结构：{state["table_schemas"]}
用户问题：{state["question"]}
""")
    resp = llm.invoke([sys_prompt, user_prompt])
    new_sql = resp.content.strip()
    print(f"生成SQL：{new_sql}")
    return {**state, "sql": new_sql, "error_msg": ""}

# ------------------------------
# 子Agent4：SQL校验Agent
# ------------------------------
def sql_validator_agent(state: AgentState) -> AgentState:
    print("\n🤖【SQL校验Agent】执行")
    sql = state["sql"]
    # 简单风险拦截
    danger_words = ["delete", "update", "drop", "alter", "insert"]
    if any(w in sql.lower() for w in danger_words):
        return {**state, "is_valid": False, "error_msg": "禁止执行修改类SQL"}
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(f"EXPLAIN {sql}")
        conn.close()
        print("✅ SQL校验通过")
        return {**state, "is_valid": True, "error_msg": ""}
    except Exception as e:
        err = str(e)
        print(f"❌ SQL校验失败：{err}")
        conn.close()
        return {**state, "is_valid": False, "error_msg": err}

# ------------------------------
# 节点：执行SQL（工具节点，不是LLM Agent）
# ------------------------------
def execute_sql(state: AgentState) -> AgentState:
    print("\n🔧【SQL执行节点】")
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(state["sql"], conn)
    conn.close()
    print(f"查询返回 {len(df)} 行数据")
    return {**state, "query_df": df}

# ------------------------------
# 子Agent5：结果汇总Agent
# ------------------------------
def summary_agent(state: AgentState) -> AgentState:
    print("\n🤖【结果汇总Agent】执行")
    sys_prompt = SystemMessage(content="你是数据分析助手，根据查询结果，用通顺中文回答用户问题。")
    user_prompt = HumanMessage(content=f"""
用户原始问题：{state["question"]}
查询结果：{state["query_df"].to_string()}
""")
    resp = llm.invoke([sys_prompt, user_prompt])
    ans = resp.content.strip()
    print(f"\n✅ 最终回答：{ans}")
    return {**state, "final_answer": ans}

# ------------------------------
# 路由函数：校验之后分支判断
# ------------------------------
def route_after_validate(state: AgentState) -> str:
    if state["is_valid"]:
        return "execute_sql"
    if state["retry_times"] >= 2:
        print("⚠️ 重试次数耗尽，终止")
        return END
    # 重试，交给生成Agent改写
    state["retry_times"] += 1
    return "sql_generator_agent"

# ------------------------------
# 图构建
# ------------------------------
builder = StateGraph(AgentState)
# 注册各个Agent节点
builder.add_node("db_parser", db_parser)
builder.add_node("table_selector_agent", table_selector_agent)
builder.add_node("schema_reader_agent", schema_reader_agent)
builder.add_node("sql_generator_agent", sql_generator_agent)
builder.add_node("sql_validator_agent", sql_validator_agent)
builder.add_node("execute_sql", execute_sql)
builder.add_node("summary_agent", summary_agent)

# 连线
builder.add_edge(START,"db_parser")
builder.add_edge("db_parser", "table_selector_agent")
builder.add_edge("table_selector_agent", "schema_reader_agent")
builder.add_edge("schema_reader_agent", "sql_generator_agent")
builder.add_edge("sql_generator_agent", "sql_validator_agent")

# 条件分支：校验后路由
builder.add_conditional_edges(
    "sql_validator_agent",
    route_after_validate,
    {
        "execute_sql": "execute_sql",
        "sql_generator_agent": "sql_generator_agent",
        END: END
    }
)
builder.add_edge("execute_sql", "summary_agent")
builder.add_edge("summary_agent", END)

graph = builder.compile()

# ------------------------------
# 运行入口
# ------------------------------
if __name__ == "__main__":
    question = "统计每个艺术家的专辑数量，按专辑数量降序排序"
    result = graph.invoke({
        "question": question,
        "all_tables": [],
        "selected_tables": "",
        "table_schemas": "",
        "sql": "",
        "is_valid": False,
        "error_msg": "",
        "query_df": pd.DataFrame(),
        "final_answer": "",
        "retry_times": 0
    })
    print("\n===== 多Agent流程结束 =====")
    print("Final Answer：", result["final_answer"])
