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

## הקמת השרת ובניית ה-EXE להפצה
ראה **`docs/LICENSE_SERVER_SETUP_HE.md`** — התקנה ב-VPS עם HTTPS (Caddy), ניהול (`--list/--code/--check/--revoke/--backup`),
בניית ה-EXE דרך GitHub Actions ("Friends Edition release") עם כתובת השרת והמפתח הציבורי, עלויות ומה עדיין חסר.
בנייה מקומית: `build\windows\build_friends.bat https://license.<domain> <public_key_hex>` ואז
`python tools\check_friends_binary.py dist\MapleFlipHunterFriends.exe` (חובה: מסרב ל-localhost/placeholder).

## הוראות לחברים (להעתקה)
1. הורד את `MapleFlipHunterFriends.exe` מהקישור שקיבלת. אם Chrome מסמן "לא נפוץ" → ⋮ → Keep. אם Windows מציג
   "Windows protected your PC" → More info → Run anyway (אין חתימת קוד; זה EXE חדש וקטן, לא וירוס).
2. הפעל. בשלושת הימים הראשונים הכול עובד (Free Trial). בפס התחתון כתוב כמה נשאר.
3. כשנגמר — מופיע מסך נעילה עם המייל של דוד ו-Copy Email. שלח לו את **12 התווים של Machine** שמופיעים במסך; תקבל קוד
   `XXXX-XXXX-XXXX` → הכנס בשדה → Activate. קוד 14 יום או לצמיתות, למחשב אחד.
4. התוכנה רק **מתריעה** על מודעות זולות (קול + כרטיס עם המיקום). היא לא נוגעת במשחק, לא מצלמת מסך, לא קוראת זיכרון.
5. עדכונים: כשיש גרסה חדשה תופיע הצעה; הרישיון נשמר.

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
