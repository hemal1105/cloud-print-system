# test_db.py
import os
from dotenv import load_dotenv
import pyodbc

load_dotenv()

server   = os.getenv("AZURE_SQL_SERVER")
database = os.getenv("AZURE_SQL_DATABASE")
user     = os.getenv("AZURE_SQL_USER")
password = os.getenv("AZURE_SQL_PASSWORD")

print(f"Server:   {server}")
print(f"Database: {database}")
print(f"User:     {user}")
print(f"Password: {'*' * len(password) if password else 'NOT SET'}")

conn_str = (
    f"Driver={{ODBC Driver 18 for SQL Server}};"
    f"Server=tcp:{server},1433;"
    f"Database={database};"
    f"Uid={user};"
    f"Pwd={password};"
    f"Encrypt=yes;"
    f"TrustServerCertificate=no;"
    f"Connection Timeout=30;"
)

print(f"\nConnection string (no password): Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{server},1433;Database={database};Uid={user};Encrypt=yes;")

try:
    conn = pyodbc.connect(conn_str)
    print("\n✅ Connection successful!")
    conn.close()
except Exception as e:
    print(f"\n❌ Connection failed: {e}")