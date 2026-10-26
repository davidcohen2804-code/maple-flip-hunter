# שרת הרישוי בחינם — Cloudflare Workers + D1 (תקציב 0 ₪)

אין VPS, אין דומיין, אין כרטיס אשראי. שרת הרישוי רץ כ‑Worker של Cloudflare בתוכנית החינמית, עם מסד D1 חינמי,
בכתובת HTTPS `https://mfh-license.<השם-שלך>.workers.dev`. ה‑EXE לחברים נבנה אוטומטית מול הכתובת הזאת.

## 1. האם זה נכנס במכסה החינמית? (חישוב מראש)

| מכסה חינמית (מתאפסת כל יום ב‑00:00 UTC) | כמה אנחנו צורכים |
|---|---|
| Workers: **100,000 בקשות/יום** | כל מחשב: 1 בהפעלה + 1 כל 10 דק' כשהתוכנה פתוחה → **מקסימום 145/יום למחשב** (24 שעות רצוף). 30 חברים = ~4,400 (4%). גבול: ~690 מחשבים רצופים |
| D1: **100,000 כתיבות שורה/יום** | הקוד כותב `last_seen` **לכל היותר פעם בשעה** למחשב → ≤ 24/יום למחשב. 30 חברים = ~720 (0.7%). מונה שימוש נכתב בקבוצות של 25 |
| D1: **5,000,000 קריאות שורה/יום** | ~1–2 שורות לבקשה → זניח |
| D1: **5 GB** | שורה למחשב + שורה לקוד → קילובייטים |
| CPU: 10 ms לבקשה | חתימת Ed25519 + שאילתה אחת: ~1–2 ms |
| כתובת workers.dev + תעודת HTTPS | חינם, אוטומטי |
| Rate limiting binding (60 בקשות/דקה ל‑IP) | חינם |

**אין צורך בשדרוג בתשלום.** בתוכנית החינמית אין אמצעי תשלום, ולכן **לא ייתכן חיוב** — חריגה רק מחזירה שגיאה עד חצות UTC.

**מה קורה אם בכל זאת חורגים?** הבקשות נכשלות עד האיפוס היומי. האפליקציה אצל החבר **לא נופלת**: היא ממשיכה על
הטוקן החתום האחרון עד 24 שעות, ובפס המצב מופיע בבירור
`⚠ licence server unreachable — works offline for 23 h 50 min more`. כיוון שהאיפוס הוא תוך ≤ 24 שעות, איש לא ננעל
בגלל חריגה. אצלך: `python server\cf_admin.py usage` מראה בקשות/יום (משוער) ומזהיר מעל 80%; מספרים מדויקים בלוח
Cloudflare → Workers & Pages → mfh-license → Metrics.

## 2. מה אני צריך ממך (פעם אחת, הכול חינם, ~10 דקות)

1. **חשבון Cloudflare חינמי** — https://dash.cloudflare.com/sign-up (מייל + סיסמה; **אין** צורך בכרטיס אשראי. לא לבחור תוכנית בתשלום).
2. **תת‑דומיין workers.dev** — בלוח: Workers & Pages → Overview → "Set up a subdomain" (שם חופשי, למשל `davidc`). הכתובת תהיה `https://mfh-license.davidc.workers.dev`.
3. **Account ID** — מופיע בצד ימין בעמוד Workers & Pages → Overview.
4. **API Token** — Profile (למעלה מימין) → API Tokens → Create Token → Template **"Edit Cloudflare Workers"** → ב‑Permissions להוסיף **Account · D1 · Edit** → Continue → Create. להעתיק את הטוקן (מוצג פעם אחת).
5. **טוקן מנהל שלך** — תמציא מחרוזת ארוכה ואקראית (לפחות 24 תווים). זה המפתח שבו אתה יוצר קודים.
6. ב‑GitHub של הריפו: Settings → Secrets and variables → Actions → New repository secret, שלושה סודות:
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `MFH_ADMIN_TOKEN`.

> אני לא יכול לפתוח חשבונות או ליצור טוקנים במקומך. אם תעשה 1–6 ותאשר — השאר אוטומטי.

## 3. הפעלה (לחיצה אחת)

GitHub → Actions → **"Deploy licence server (Cloudflare free) + build Friends EXE"** → Run workflow.

מה קורה בפנים:
1. יוצר/מאתר את מסד D1 `mfh-licenses` ומריץ את הסכמה.
2. מפרסם את ה‑Worker ומוציא את כתובת ה‑workers.dev.
3. **בפעם הראשונה בלבד**: מייצר זוג מפתחות Ed25519 **בתוך ה‑Runner**, שומר את המפתח הפרטי אך ורק כ‑Secret של ה‑Worker
   (`LICENSE_PRIVATE_KEY`), לא מדפיס ולא שומר אותו בשום מקום אחר; המפתח הציבורי נשמר במסד. בהרצות הבאות המפתח **נשמר**
   (אחרת הטוקנים של החברים היו נפסלים). שומר גם את `ADMIN_TOKEN`.
4. בדיקות מול השרת החי: health, חתימת טוקן מאומתת בפייתון עם המפתח הציבורי, `/admin` מסרב בלי טוקן.
5. בונה **`MapleFlipHunterFriends.exe`** (Windows) עם הכתובת והמפתח הציבורי, מריץ את שער הבינארי (רק https אמיתי, בלי
   localhost/placeholder/טוקני בדיקה), כותב `MapleFlipHunterFriends.version.txt` (גרסה + SHA256), ומריץ `--license-check`
   מול השרת האמיתי. התוצר ב‑Artifacts של ההרצה.

**איפה המפתחות?** פרטי — Secret של Cloudflare בלבד (לא בריפו, לא ב‑EXE, לא בלוגים). ציבורי — במסד וב‑EXE. טוקן מנהל —
אצלך + Secret ב‑GitHub + Secret ב‑Worker. אם המפתח הפרטי אבד (מחיקת ה‑Worker): הרצה נוספת תייצר חדש, ואז צריך EXE חדש לחברים.

## 4. ניהול מהמחשב שלך

```bat
set MFH_SERVER=https://mfh-license.<השם-שלך>.workers.dev
set MFH_ADMIN_TOKEN=<הטוקן שהמצאת>
python server\cf_admin.py code 14      :: קוד חד-פעמי ל-14 יום → XXXX-XXXX-XXXX
python server\cf_admin.py code 0       :: קוד לצמיתות
python server\cf_admin.py list         :: כל המחשבים + קודים שלא נוצלו
python server\cf_admin.py check 3fa9c1 :: מצב מחשב (12 התווים ממסך הנעילה של החבר)
python server\cf_admin.py revoke 3fa9c1  / unrevoke 3fa9c1
python server\cf_admin.py usage        :: בקשות/יום מול המכסה
python server\cf_admin.py update 0.13.1 https://github.com/<you>/MapleFlipHunter/releases/download/v0.13.1/MapleFlipHunterFriends.exe dist\MapleFlipHunterFriends.exe
```
(אפשר גם `server\cf_admin.json` עם `{"server": "...", "admin_token": "..."}` — הקובץ ב‑.gitignore.)

כללים (נאכפים בשרת, לפי שעון השרת): 72 שעות ניסיון מההפעלה הראשונה; קוד 14 יום חד‑פעמי, נקשר למחשב הראשון; קוד
לצמיתות לא יורד בדרגה; `revoke` נועל גם lifetime; `unrevoke` מחזיר לכללי הניסיון (לתת קוד חדש); רישיון לפי מחשב ולא
לפי גרסה — עדכונים לא מאבדים רישיון.

## 5. גיבויים (חינם)

- D1 **Time Travel**: שחזור לכל נקודה ב‑30 הימים האחרונים, מובנה.
- Workflow **"Licence database backup"**: כל יום שני מייצא את המסד ל‑SQL ושומר כ‑Artifact ב‑GitHub ל‑90 יום (וגם ידנית).

## 6. מה נבדק אוטומטית

- CI הרגיל (Windows): ה‑Worker רץ **מקומית** (wrangler dev / miniflare — בלי חשבון ובלי רשת) וה‑EXE עובר את כל המסלול:
  ניסיון → 73 שעות → נעילה (קוד יציאה 3) → קוד 14 יום → רישיון → 15 יום → נעילה → קוד לצמיתות → ביטול → נעילה.
- בדיקות יחידה: אותו מודל רישוי בשרת הייחוס בפייתון (זיוף טוקן, קוד במחשב אחר, שעון אחורה, 24 שעות offline, מניפסט מזויף).
- מניפסט עדכון שנחתם ב‑Worker מאומת בפייתון (אותו JSON קנוני).

## 7. הוראות לחברים

ב‑`docs/FRIENDS_EDITION_HE.md` ("הוראות לחברים"). בקיצור: להוריד, Run anyway אם SmartScreen שואל (אין חתימת קוד — זה עולה
כסף, לא נרכש), 3 ימים חינם, במסך הנעילה לשלוח לך את 12 תווי Machine → קוד.

## 8. מה עדיין חסר ממך

- סעיף 2 (חשבון Cloudflare, תת‑דומיין, Account ID, API Token, טוקן מנהל, 3 סודות ב‑GitHub) — אחרי זה הרצה אחת של ה‑workflow.
- בדיקה על Windows 10/11 Home אמיתי (ה‑CI הוא Windows Server) — אצלך או אצל חבר ראשון.
- חלופה אם לא רוצים Cloudflare: `docs/LICENSE_SERVER_SETUP_HE.md` (VPS בתשלום — לא רלוונטי לתקציב 0).
