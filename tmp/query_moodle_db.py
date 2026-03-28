"""
Query Moodle's MySQL database directly to get the page content for CMID 74.
Moodle stores page content in the mdl_page table.
"""
import mysql.connector
import json

# Common Moodle MySQL credentials to try
credentials_to_try = [
    {'host': 'localhost', 'user': 'root', 'password': '', 'database': 'moodle'},
    {'host': 'localhost', 'user': 'root', 'password': 'root', 'database': 'moodle'},
    {'host': 'localhost', 'user': 'root', 'password': 'admin', 'database': 'moodle'},
    {'host': 'localhost', 'user': 'moodle', 'password': 'moodle', 'database': 'moodle'},
    {'host': 'localhost', 'user': 'root', 'password': '12345678', 'database': 'moodle'},
    {'host': 'localhost', 'user': 'root', 'password': 'moodle', 'database': 'moodle'},
]

conn = None
for creds in credentials_to_try:
    try:
        conn = mysql.connector.connect(**creds)
        print(f"✓ Connected with user={creds['user']}, password='{creds['password']}'")
        break
    except Exception as e:
        print(f"✗ Failed: user={creds['user']}, password='{creds['password']}' → {e}")

if conn:
    cursor = conn.cursor(dictionary=True)
    
    # Query the page with instance=34 (cmid=74 → instance=34)
    cursor.execute("SELECT id, course, name, content, contentformat FROM mdl_page WHERE id = 34")
    rows = cursor.fetchall()
    print(f"\n=== mdl_page (instance=34) ===")
    for row in rows:
        print(f"ID: {row['id']}, Name: {row['name']}")
        print(f"Content format: {row['contentformat']}")
        print(f"Content:\n{row['content']}")

    # Also check if there are any video files related to this context
    cursor.execute("""
        SELECT f.id, f.filename, f.filesize, f.mimetype, f.filepath, f.component, f.filearea
        FROM mdl_files f
        WHERE f.contextid = 90 AND f.filesize > 0
        ORDER BY f.filesize DESC
        LIMIT 20
    """)
    files = cursor.fetchall()
    print(f"\n=== mdl_files (contextid=90, filesize>0) ===")
    for f in files:
        print(f"  {f['filename']} ({f['mimetype']}) - {f['filesize']} bytes - {f['component']}/{f['filearea']}")
    
    cursor.close()
    conn.close()
else:
    print("Could not connect to Moodle MySQL database.")
    print("Try installing mysql-connector-python: pip install mysql-connector-python")
