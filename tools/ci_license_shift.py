"""CI helper: move the trial start 73 h back, or (arg 'activation') make the 14-day activation expire."""
import sqlite3, sys, time
db = sys.argv[1] if len(sys.argv) > 1 else "server/licenses.sqlite3"
what = sys.argv[2] if len(sys.argv) > 2 else "trial"
c = sqlite3.connect(db)
if what == "activation":
    c.execute("UPDATE machines SET licensed_until=? WHERE licensed_until>0", (time.time() - 60,))
else:
    c.execute("UPDATE machines SET first_seen=?", (time.time() - 73 * 3600,))
c.commit(); c.close()
print("shifted:", what)
