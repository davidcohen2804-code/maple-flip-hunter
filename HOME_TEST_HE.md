# בדיקה בבית — Maple Flip Hunter

## א. הפעלה (קובץ מוכן)
1. הורד את `MapleFlipHunter-win64.zip` מעמוד ה-Actions במאגר (הקישור נשלח בצ'אט) וחלץ לתיקייה, למשל `C:\MapleFlipHunter`.
2. לחץ פעמיים על `MapleFlipHunter.exe`. אם SmartScreen מזהיר (הקובץ לא חתום): "מידע נוסף" → "הפעל בכל זאת".
3. בראש המסך תראה את המצב:
   * **LIVE** = נתונים טריים מגיעים.
   * בשורת "Connected sources" צריך להופיע `cozy.markets: ONLINE`. אם מופיע `AUTH REQUIRED` — Cloudflare חוסם את התוכנה גם מהבית (ראה סעיף ג).
4. לחץ **Test alarm** כדי לוודא שהצליל נשמע.

## ב. מדידת הזמן האמיתי מזיהוי מודעה ועד צפצוף
השאר את התוכנה פתוחה 10–20 דקות. כשתישמע התראה, לחץ על השורה: בפאנל הפרטים מופיעים
"Source observed" (מתי הסורק של המקור ראה את החנות) ו-"Analysis … alerted" (מתי צפצפה).
בפס העליון "Avg source→alert latency" הוא הממוצע האמיתי של כל ההתראות בהפעלה הזו.

## ג. בדיקת cozy.markets מהמחשב הביתי (מדויקת, עם דוח)
דורש Python 3.11+ מותקן (python.org, לסמן "Add python.exe to PATH").
1. חלץ את `MapleFlipHunter_FULL_PROJECT.zip`.
2. לחץ פעמיים על `run_live_check.bat` (כ-5 דקות; תשמע שני צלילים ותראה הודעת Windows).
3. פתח `reports\VERIFICATION_REPORT.md`:
   * `cozy.markets: OK` + גיל הסריקה החדשה ביותר → החיבור עובד מהבית.
   * `cozy.markets: BLOCKED` → Cloudflare חוסם גם אותך; במקרה כזה התוכנה תעבוד רק עם maplestory.watch (איטי יותר, 2–5 דקות), ואפשר לפנות למפעילי cozy לבקשת גישה.
   * `audio playback: VERIFIED` → השמע נבדק באמת אצלך.
   * בחלק "Packaged EXE headless run": `source→alert` = ההתפלגות (מינימום/חציון/p95) של השניות בין חותמת הזמן של המקור להתראה.
4. שלח לי את תיקיית `reports\` ואעדכן את המסמכים עם המספרים האמיתיים.
