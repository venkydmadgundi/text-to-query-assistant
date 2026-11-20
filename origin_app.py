import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from langchain_community.agent_toolkits import create_sql_agent
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine
import sqlite3
import streamlit as st
from langchain_groq import ChatGroq
from dotenv import load_dotenv
import pandas as pd
import json
# from langchain.agents import AgentType

load_dotenv()
groq_api_key = os.environ["GROQ_API_KEY"]

st.title("HR Assitant for Text to SQL")

if not groq_api_key:
    st.info("Please provide your Groq API key to continue")
    st.stop()


dbfilepath = "db/employee.db"
creator = lambda: sqlite3.connect(f"file:{dbfilepath}?mode=ro", uri=True)
database = SQLDatabase(create_engine("sqlite:///", creator=creator))
st.success("Database connected successfully!")

conn = sqlite3.connect("db/employee.db")

# Get column names from employee table
cursor = conn.execute("PRAGMA table_info(employees)")

st.sidebar.title("Employee Table Fields")
for col in cursor.fetchall():
    st.sidebar.write(col[1])

conn.close()

llm = ChatGroq(api_key=groq_api_key,
               model_name="openai/gpt-oss-120b",
               temperature=0,
               streaming=True)

agent = create_sql_agent(
    llm=llm,
    db=database,
    agent_type="tool-calling",
    verbose=True,
    max_iterations=5,
    handle_parsing_errors=True,
    agent_executor_kwargs={
        "return_intermediate_steps": True
    }
)

user_query = st.chat_input(placeholder="Ask anything from the database")
if user_query:
    response = agent.invoke({"input": user_query})
    print(response)
    if response:
        st.write(response["output"])
        steps = response["intermediate_steps"]

        query = next(
            (
                action.tool_input.get("query")
                for action, _ in response["intermediate_steps"]
                if hasattr(action, "tool_input")
                and action.tool_input.get("query")
            ),
            None
        )

        st.code(query, language="sql")