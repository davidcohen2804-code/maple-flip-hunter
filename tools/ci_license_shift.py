"""CI helper: pretend the trial started 73 h ago (server side)."""
import sqlite3, sys, time
db = sys.argv[1] if len(sys.argv) > 1 else "server/licenses.sqlite3"
c = sqlite3.connect(db); c.execute("UPDATE machines SET first_seen=?", (time.time() - 73 * 3600,)); c.commit(); c.close()
print("trial start moved back 73 h")
