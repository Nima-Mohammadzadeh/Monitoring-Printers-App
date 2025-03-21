import sqlite3
import os

# Use an absolute path to ensure you're working in the correct directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "job_tracking.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Create a 'jobs' table if it doesn't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer TEXT,
        job_size INTEGER,
        roll_size INTEGER,
        label_type TEXT,
        printer TEXT,
        ticket_number TEXT,
        completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')
conn.commit()
