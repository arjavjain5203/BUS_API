import mysql.connector
import datetime
import os
from dotenv import load_dotenv
from google.generativeai import configure, GenerativeModel
from collections import deque
from fastapi import FastAPI
from pydantic import BaseModel

# -------------------------
# 1. Load .env config
# -------------------------
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

# -------------------------
# 2. Configure Gemini
# -------------------------
configure(api_key=API_KEY)
model = GenerativeModel("gemini-1.5-flash")

# -------------------------
# 3. Configure MySQL
# -------------------------
db = mysql.connector.connect(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASSWORD,
    database=DB_NAME
)
cursor = db.cursor(dictionary=True)

# -------------------------
# 4. Memory
# -------------------------
chat_history = deque(maxlen=5)

def get_chat_context():
    return "\n".join([f"User: {u}\nBot: {b}" for u, b in chat_history])

# -------------------------
# 5. Prompt Helpers
# -------------------------

SCHEMA_PROMPT = """
You are a SQL assistant for a Punjab transport database.
Rules:
- Only use these tables and columns:
  users(user_id, name, age, mobile_no, email, region_of_commute, created_at)
  busstops(stop_id, stop_name, location, region)
  routes(route_id, route_name, start_stop_id, end_stop_id, distance_km)
  buses(bus_id, bus_number, capacity, current_location, route_id, status)
  drivers(driver_id, name, mobile_no, bus_id, location, shift_start, shift_end)
  tickets(ticket_id, user_id, bus_id, route_id, source_stop_id, destination_stop_id, fare, purchase_time)
  notifications(notification_id, user_id, type, message, sent_at)
  chatlogs(chat_id, user_id, message_text, response_text, created_at)
  routestops(id, route_id, stop_id, stop_order)

- Do NOT use columns that don’t exist.
- Always use LIKE instead of = when filtering stop_name or location.
- For bus availability queries, return bus_number, route_name, current_location, and status.
- If data is not found, return an empty result (do not invent).
- Always include LIMIT 3 to avoid large results.

Examples:

User: Next bus from ISBT Chandigarh to Ludhiana?
SQL:
SELECT b.bus_number, r.route_name, b.current_location, b.status
FROM routes r
JOIN buses b ON r.route_id = b.route_id
JOIN routestops rs1 ON r.route_id = rs1.route_id
JOIN busstops bs1 ON rs1.stop_id = bs1.stop_id
JOIN routestops rs2 ON r.route_id = rs2.route_id
JOIN busstops bs2 ON rs2.stop_id = bs2.stop_id
WHERE bs1.stop_name LIKE '%Chandigarh%'
  AND bs2.stop_name LIKE '%Ludhiana%'
  AND b.status = 'Running'
LIMIT 3;

User: Show all buses from Chandigarh
SQL:
SELECT b.bus_number, r.route_name, b.current_location, b.status
FROM buses b
JOIN routes r ON b.route_id = r.route_id
JOIN routestops rs ON r.route_id = rs.route_id
JOIN busstops bs ON rs.stop_id = bs.stop_id
WHERE bs.stop_name LIKE '%Chandigarh%'
LIMIT 3;
"""


def generate_sql(user_input: str) -> str:
    context = get_chat_context()
    response = model.generate_content(
        f"{SCHEMA_PROMPT}\nConversation so far:\n{context}\n\nUser: {user_input}\nSQL:"
    )
    return response.text.strip().strip("```sql").strip("```")

def format_response(raw_results: list, user_input: str) -> str:
    context = get_chat_context()
    response = model.generate_content(
        f"Conversation so far:\n{context}\n\nUser just asked: {user_input}\nHere are SQL query results:\n{raw_results}\n"
        f"Format this as a clear SMS/WhatsApp style reply:"
    )
    return response.text.strip()

# -------------------------
# 6. FastAPI Setup
# -------------------------
app = FastAPI(title="Punjab Bus Assistant API")

class ChatRequest(BaseModel):
    user_id: int = 1
    message: str

@app.get("/")
def root():
    return {"message": "Punjab Bus Assistant API is running 🚍"}


@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    user_input = req.message
    user_id = req.user_id

    sql_query = generate_sql(user_input)

    try:
        cursor.execute(sql_query)
        results = cursor.fetchall()
    except Exception as e:
        results = {"error": str(e)}

    response_text = format_response(results, user_input)

    # Update memory
    chat_history.append((user_input, response_text))

    # Save in DB
    insert_chat = """
         INSERT INTO chatlogs (user_id, message_text, response_text, created_at)
         VALUES (%s, %s, %s, %s)
     """
    cursor.execute(insert_chat, (user_id, user_input, response_text, datetime.datetime.now()))
    db.commit()

    return {
        "user_message": user_input,
        "sql_query": sql_query,
        "results": results,
        "bot_response": response_text
    }
