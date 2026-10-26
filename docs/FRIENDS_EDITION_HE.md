# Friends Edition — מה זה, מה לא, ואיך מפיצים

## עקרונות (תואמי-הרשאות)
* **EXE יחיד** (`MapleFlipHunterFriends.exe`, onefile, בלי Python). גרסת המפתח שלך (`MapleFlipHunter.exe`) ללא הגבלה.
* **כל משתמש מושך נתונים ישירות מהמקורות בעצמו** (maplestory.watch; Club FM רק אחרי אישור) + Physical Scanner מקומי.
  **ה-backend שלך לא מפיץ נתוני שוק** — אין לנו הרשאת הפצה מחדש של נתוני הספקים, ולכן זה לא נבנה.
* ה-backend שלך משמש **לרישוי, הפעלה ועדכונים בלבד**. ב-EXE אין סודות: רק כתובת השרת והמפתח **הציבורי** שלו.
* שמות המקורות נשארים גלויים בפאנל הפרטים (קישור למודעה) — אין הסתרת ייחוס.

## שלושה שלבי רישיון
1. **Free Trial** — 72 שעות מההפעלה הראשונה.
2. **14-Day Activation** — קוד חד-פעמי (`--code 14`) פותח 14 יום מרגע ההפעלה.
3. **Lifetime** — קוד חד-פעמי (`--code 0`) פותח לצמיתות ומחליף כל רישיון זמני.
כל קוד למכונה אחת; השעון הקובע הוא של השרת; בפס המצב מוצג כמה נשאר.

## רישיון ניסיון 72 שעות
* מתחיל ב**הפעלה הראשונה**, לפי **שעון השרת** (`POST /v1/activate`), כפוי למזהה מכונה (hash של MachineGuid + סידורי כונן + תאריך התקנת Windows).
  התקנה מחדש / מחיקת `%APPDATA%` / מחיקת הרישום לא מאפסים — השרת זוכר את המכונה.
* הלקוח בודק מול השרת בכל הפעלה וכל 10 דק'; מקבל token חתום (Ed25519) שהוא יכול לאמת גם בלי רשת.
* הזזת שעון אחורה = נעילה עד שיש קשר לשרת; בלי קשר לשרת מעל 24 שעות = נעילה.
* מסך נעילה: "Your 3-day free trial has expired." + המייל שלך + **Copy Email** + שדה קוד הפעלה + Check again.
* קודים: `python server\license_server.py --code 30` (30 יום; `0` = לכל החיים). קוד תקף למכונה אחת.

## הקמת השרת (פעם אחת)
1. במחשב/VPS שלך: `pip install cryptography` ואז `python server\license_server.py --init` → מדפיס את המפתח הציבורי.
2. `python server\license_server.py --serve 8787` (רצוי מאחורי HTTPS: Caddy/nginx). `private_key.pem` נשאר רק אצלך.
3. בנייה: `build\windows\build_friends.bat https://license.your.host <public_key_hex>` → `dist\MapleFlipHunterFriends.exe`.
4. `--list` מציג מכונות/ניסיון/סטטוס.

## עדכון מאובטח
`python server\license_server.py --update 0.8.1 https://your.host/MapleFlipHunterFriends.exe dist\MapleFlipHunterFriends.exe`
מפרסם manifest חתום; הלקוח מאמת חתימה + sha256 לפני שהוא מחליף את עצמו. manifest/קובץ לא חתומים נזרקים.

## מה נבדק ב-CI (Windows)
בנייה onefile → שרת רישוי מקומי → הפעלה ראשונה = trial 72 ש' → הזזת תחילת הניסיון 73 ש' אחורה בשרת = נעילה (קוד יציאה 3)
→ קוד הפעלה = licensed. ובדיקות יחידה: זיוף token, קוד על מכונה אחרת, שעון אחורה, 24 ש' offline, manifest מזויף, hash לא תואם.

## מגבלות כנות
* מי שמזייף את מזהה המכונה (וירטואליזציה) יקבל ניסיון חדש — אין הגנה מוחלטת בלי חומרה ייעודית.
* ה-EXE מכיל את הקוד (PyInstaller) — ניתן לחילוץ; אין בו סודות, ולכן זה לא פוגע ברישוי (הרישוי נאכף בשרת).
* "התקנה נקייה" נבדקה על runner של GitHub (Windows Server), לא על Windows Home של משתמש קצה.
