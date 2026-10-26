# FM Watcher כמקור שני — התקנה ואימות (5 דקות)

## התקנה
1. הורד FM Watcher: https://maplefm.gg/contribute.html → **Download** (`FMWatcher.zip`), חלץ לתיקייה (למשל `Documents\FMWatcher`).
2. לחץ פעמיים על `install.bat` (מתקין Python ו-Tesseract). סגור כשכתוב Done.
3. פתח את המשחק, ואז לחץ פעמיים על `start.bat` — השאר את החלון פתוח ברקע.
4. במשחק: פתח פעם אחת את חלון **Change Channel** (כך הסורק יודע את הערוץ), והשאר את ה-minimap גלוי (חדר ה-FM).
5. ב-Maple Flip Hunter: **Settings → Sources → FM Watcher folder → Browse…** → בחר את התיקייה עם `start.bat` → **Save**.
   הסורק מופעל מחדש לבד.

## מה צריך לראות — לפי הסדר
| שלב | מה לראות | אם לא |
|---|---|---|
| 1. חיבור | בפס המצב: `FM Watcher (your own scans): ONLINE — reading watcher_data_v2.json` | `UNAVAILABLE … not found`: התיקייה לא נכונה. `… is FM Watcher running`: הרץ `start.bat` ופתח חנות אחת. |
| 2. קליטה תוך שניות | פתח חנות ב-FM, גלול עד הסוף. תוך ≤ 35 שניות (FM Watcher כותב את הקובץ עד 30 ש' אחרי הקריאה, התוכנה קוראת כל 5 ש') מספר **New listings (session)** עולה | בדוק ב-`watcher.log` של FM Watcher שהוא קרא את החנות; `python tools\check_fmwatcher.py "<תיקייה>"` מדפיס מה התוכנה רואה. |
| 3. מחיר ומיקום | בטבלה (אם עבר סינון) או בפאנל: Location `CH x · FM y`, Shop = שם המוכר, Buy = מחיר ליחידה | חסר CH: לא פתחת Change Channel; חסר FM: minimap מוסתר. |
| 4. צליל אוטומטי | מציאה שעוברת את הסינון → צליל + כרטיס צף, גם כשהמשחק פתוח (borderless). בלוג: `ALERT_CREATED … AUDIO_STARTED thread=gui` | Settings → **🧪 Test Automatic Alert** צריך לצלצל; אם לא — `AUDIO_` בלוג. |
| 5. בלי כפילויות | חנות שגם maplestory.watch רואה: שורה אחת בטבלה; בלוג `duplicate` ולא התראה שנייה; `Source` בפאנל הוא המקור שראה ראשון | — |

לוגים: `%APPDATA%\MapleFlipHunter\logs\maple_flip_hunter.log` (חפש `fmwatcher`, `ALERT_CREATED`, `duplicate`).

הערות: FM Watcher קורא רק את המסך שלך ומעלה בעצמו את המחירים ל-Club FM — Maple Flip Hunter לא משנה זאת ולא שולח כלום.
אימות זמינות למודעות מהסריקות שלך קורה רק כשאתה פותח שוב את אותה חנות.
