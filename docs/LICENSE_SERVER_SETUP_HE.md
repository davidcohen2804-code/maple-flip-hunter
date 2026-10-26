# שרת הרישוי — התקנה, ניהול, עלויות (גרסת חברים)

> **חלופה חינמית לחלוטין (ללא VPS/דומיין): `docs/CLOUDFLARE_FREE_SETUP_HE.md`.** מסמך זה מתאר את מסלול ה-VPS בתשלום.

מסמך זה מתאר **את כל מה שצריך כדי להפיץ באמת** את `MapleFlipHunterFriends.exe`. השרת מטפל ברישוי ובעדכונים בלבד —
הוא **לא** מעביר נתוני שוק של אף ספק.

## 1. מה חסר כדי להתחיל (מה אני צריך ממך)

אין לי שרת, דומיין או גישה משלי, ולא אמציא כתובת. כדי להשלים נדרשים ממך בדיוק שלושה דברים:

| # | מה | למה | איך |
|---|----|-----|-----|
| 1 | **VPS קטן עם Ubuntu 22.04/24.04** (1 vCPU, 1 GB RAM מספיק) | מריץ את `license_server.py` + Caddy | Hetzner CX22 / DigitalOcean Basic / Vultr — נרשמים, בוחרים Ubuntu, מקבלים IP ו‑root SSH |
| 2 | **דומיין (או תת‑דומיין) שמצביע ל‑IP של ה‑VPS** | HTTPS אמיתי עם תעודה אוטומטית (Let's Encrypt) | ברשם הדומיינים: רשומת `A` של `license.<הדומיין שלך>` → IP של השרת |
| 3 | **גישת SSH לשרת** (או שאתה מריץ בעצמך את הפקודות למטה) | ההתקנה הראשונית + יצירת קודים | `ssh root@<IP>` |

**אופציונלי:** תעודת חתימת קוד (Code Signing) — בלעדיה Windows SmartScreen יציג אזהרה "Unknown publisher" בהרצה ראשונה (ראה §7).

> ברגע שיש דומיין שעובד ומפתח ציבורי מהשרת, בניית ה‑EXE היא לחיצה אחת ב‑GitHub Actions (§4).

## 2. התקנת השרת (פעם אחת, ~5 דקות)

על ה‑VPS, כ‑root:

```bash
git clone https://github.com/<המשתמש שלך>/MapleFlipHunter.git /opt/mfh-src
bash /opt/mfh-src/server/deploy/install.sh license.<הדומיין-שלך>
```

הסקריפט:
- מתקין Python + `cryptography`, יוצר משתמש מערכת `mfh`;
- מריץ `license_server.py --init` → יוצר **מפתח פרטי** (`/opt/mfh/server/private_key.pem`, הרשאות 600, נשאר רק בשרת) ומפתח ציבורי (`public_key.txt`);
- מתקין שירות systemd `mfh-license` (עולה אוטומטית אחרי ריסטארט, קשור ל‑127.0.0.1:8797 בלבד);
- מתקין Caddy שמקבל HTTPS מהאינטרנט ומעביר לשרת המקומי; התעודה מתחדשת לבד;
- פותח בחומת האש רק SSH/80/443;
- מדפיס בסוף את **המפתח הציבורי** — אותו מעתיקים לבניית ה‑EXE.

בדיקה: `curl https://license.<הדומיין>/v1/health` → `{"ok": true, ...}`.

## 3. ניהול יומיומי (כל הפקודות ב‑`/opt/mfh/server`)

```bash
cd /opt/mfh/server
sudo -u mfh python3 license_server.py --list              # כל המחשבים: תחילת ניסיון, גרסה, מצב (trial/expired/licensed)
sudo -u mfh python3 license_server.py --code 14           # קוד חד‑פעמי ל‑14 יום  → מדפיס XXXX-XXXX-XXXX
sudo -u mfh python3 license_server.py --code 0            # קוד לצמיתות (lifetime)
sudo -u mfh python3 license_server.py --check 3fa9c1       # מצב מחשב לפי תחילת המזהה (מופיע ב‑--list ובמסך הנעילה)
sudo -u mfh python3 license_server.py --revoke 3fa9c1      # נעילת מחשב (האפליקציה ננעלת בבדיקה הבאה, עד 10 דק')
sudo -u mfh python3 license_server.py --unrevoke 3fa9c1
sudo -u mfh python3 license_server.py --backup             # גיבוי ידני; גיבוי אוטומטי כל שעה ל‑backups/ (30 אחרונים)
sudo journalctl -u mfh-license -f                           # לוג חי
```

כללי הרישוי (נאכפים בשרת, שעון השרת בלבד):
- מחשב חדש → **72 שעות ניסיון** מההפעלה הראשונה. התקנה מחדש / מחיקת קבצים לא מאפסת (המזהה נגזר מהמחשב, והשרת זוכר).
- קוד `--code 14` → 14 יום. הקוד חד‑פעמי ונקשר **למחשב הראשון שהזין אותו**; מחשב אחר שינסה יקבל "another computer".
- קוד `--code 0` → לצמיתות; לא ניתן להוריד בדרגה.
- רישיון שמור לפי מחשב, לא לפי גרסה → **עדכון האפליקציה לא מאבד רישיון**.
- מחשב `--revoke` ננעל גם אם היה לו lifetime; קוד חדש לא עוזר עד `--unrevoke`.
- הגנה: 60 בקשות/דקה לכל IP (מעבר לזה 429), גוף בקשה עד 4 KB, כל טוקן חתום Ed25519 ומכיל את מזהה המחשב ואת זמן השרת (מניעת הזזת שעון / שימוש חוזר במחשב אחר).

## 4. בניית ה‑EXE לחברים (GitHub Actions, בלי מכונת פיתוח)

Actions → **Friends Edition release** → Run workflow:
- `server_url` = `https://license.<הדומיין>`
- `public_key` = התוכן של `/opt/mfh/server/public_key.txt`

ה‑workflow: מריץ בדיקות רישוי → מסרב לכתובות placeholder/localhost → בונה `MapleFlipHunterFriends.exe` (onefile, ללא קוד מקור חשוף: PyInstaller + edition=friends) → **סורק את הבינארי** (`tools/check_friends_binary.py`): כתובת https אמיתית בלבד, מפתח ציבורי תקין, אין `127.0.0.1`/`localhost`/`demo-token`/הגדרות CI → חותם אם קיימים הסודות `WINDOWS_CERT_PFX_BASE64` + `WINDOWS_CERT_PASSWORD` → כותב `MapleFlipHunterFriends.version.txt` (גרסה + SHA256 + שרת) → מריץ `--license-check` מול השרת האמיתי → מעלה Artifact.

מה **לא** נמצא ב‑EXE: מפתח פרטי, טוקנים של ספקים, קוד הסורקים הפיזיים לא ניתן להפעלה (ראה §6), כתובות בדיקה.

## 5. עדכונים חתומים

```bash
# אחרי שהעלית את ה‑EXE החדש לכתובת ציבורית (למשל GitHub Release):
sudo -u mfh python3 license_server.py --update 0.12.0 https://github.com/<you>/MapleFlipHunter/releases/download/v0.12.0/MapleFlipHunterFriends.exe /path/to/MapleFlipHunterFriends.exe
```
השרת חותם מניפסט (גרסה, URL, SHA256). האפליקציה בודקת פעם ביום, מורידה, מאמתת SHA256 וחתימה, ומחליפה את עצמה רק באישור המשתמש. רישיון לא מושפע.

## 6. מה גרסת החברים לא עושה (מובנה, לא הגדרה)

ב‑edition=friends האפליקציה כופה בעת טעינת ההגדרות: `physical_features_enabled=False`, FM Watcher כבוי ולא מופעל אוטומטית, כתובת Club FM רק הרשמית (ועדיין OFF בלי אישור ספק). אין צילום מסך, אין קריאת זיכרון, אין פעולה במשחק — נבדק ב‑`tests/unit/test_pure_alert_mode.py` ו‑`test_friends_edition.py`.

## 7. SmartScreen / חתימת קוד

ללא תעודה: בהרצה ראשונה Windows מציג "Windows protected your PC" → **More info → Run anyway**; Chrome עשוי לסמן את ההורדה → "Keep". לחברים יש הסבר ב‑`docs/FRIENDS_EDITION_HE.md`.
עם תעודה (OV ~70–250 $ לשנה, EV ~300–400 $): מוסיפים את ה‑PFX כ‑secret ב‑GitHub והחתימה אוטומטית; EV מסיר את האזהרה מיד, OV בונה "reputation" תוך שבועות.

## 8. עלויות

| פריט | עלות | הכרחי |
|------|------|-------|
| VPS (Hetzner CX22 / DO Basic) | ~4–6 $ לחודש | כן |
| דומיין | ~10–15 $ לשנה (תת‑דומיין של דומיין קיים — 0) | כן |
| תעודת HTTPS (Let's Encrypt דרך Caddy) | 0 | כן (אוטומטי) |
| GitHub Actions (ריפו ציבורי/פרטי במכסה החינמית) | 0 | כן |
| תעודת חתימת קוד | 0 (אזהרת SmartScreen) או 70–400 $ לשנה | לא |

## 9. מה עדיין חסר (אמת, לא CI‑DEMO)

- **אין עדיין EXE מוכן להפצה** — הוא ייבנה רק אחרי שתהיה כתובת https אמיתית ומפתח ציבורי מהשרת שלך. ה‑workflow מסרב לבנות עם placeholder.
- **בדיקה על Windows 10/11 Home נקי**: ה‑CI רץ על Windows Server 2022. הדרך: מכונה וירטואלית נקייה אצלך או מחשב של חבר — להריץ `MapleFlipHunterFriends.exe --license-check probe.json` ולהעביר לי את הפלט.
- **Club FM** עדיין OFF עד אישור כתוב אמיתי מהספק (לא משתנה בגרסת חברים).
- **חתימת קוד** — רק אם תרכוש תעודה.
