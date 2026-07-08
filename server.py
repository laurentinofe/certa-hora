import base64
import calendar
import hashlib
import hmac
import json
import math
import mimetypes
import os
import secrets
import sqlite3
import sys
import threading
import urllib.parse
import zipfile
from datetime import date, datetime, time, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "ponto.db"
# Em hospedagens, o servidor precisa aceitar conexões externas. O mesmo host
# continua acessível localmente por http://127.0.0.1:8000.
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
WORKDAY_MINUTES = 8 * 60 + 30
TOLERANCE_MINUTES = 10
PUNCH_TYPES = ["ENTRADA", "SAIDA_ALMOCO", "RETORNO_ALMOCO", "SAIDA"]
SESSIONS = {}
SESSION_LOCK = threading.Lock()
APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Sao_Paulo"))


def now_local():
    return datetime.now(APP_TIMEZONE)


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def password_ok(password, encoded):
    try:
        _, rounds, salt, expected = encoded.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def init_db():
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                registration TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('EMPLOYEE','MANAGER')),
                active INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS punches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                punched_at TEXT NOT NULL,
                punch_type TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'WEB',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                punch_id INTEGER REFERENCES punches(id),
                action TEXT NOT NULL CHECK(action IN ('ADD','EDIT','DELETE')),
                requested_at_value TEXT,
                requested_type TEXT,
                reason TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
                requested_by INTEGER NOT NULL REFERENCES users(id),
                reviewed_by INTEGER REFERENCES users(id),
                requested_at TEXT NOT NULL,
                reviewed_at TEXT,
                review_note TEXT
            );

            CREATE TABLE IF NOT EXISTS overtime_justifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                work_date TEXT NOT NULL,
                minutes INTEGER NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK(status IN ('PENDING','APPROVED','REJECTED')),
                reviewed_by INTEGER REFERENCES users(id),
                reviewed_at TEXT,
                review_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, work_date)
            );

            CREATE TABLE IF NOT EXISTS day_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                work_date TEXT NOT NULL,
                category TEXT NOT NULL,
                note TEXT NOT NULL,
                manager_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                UNIQUE(user_id, work_date)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL,
                entity TEXT NOT NULL,
                entity_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                company_name TEXT NOT NULL DEFAULT 'Empresa Demonstração',
                company_document TEXT NOT NULL DEFAULT '',
                work_start TEXT NOT NULL DEFAULT '08:00',
                lunch_start TEXT NOT NULL DEFAULT '12:00',
                lunch_end TEXT NOT NULL DEFAULT '13:30',
                work_end TEXT NOT NULL DEFAULT '18:00',
                workday_minutes INTEGER NOT NULL DEFAULT 510,
                tolerance_minutes INTEGER NOT NULL DEFAULT 10,
                geofence_enabled INTEGER NOT NULL DEFAULT 0,
                geofence_latitude REAL,
                geofence_longitude REAL,
                geofence_radius_meters INTEGER NOT NULL DEFAULT 200,
                geofence_label TEXT NOT NULL DEFAULT 'Local principal',
                updated_at TEXT NOT NULL
            );
            """
        )
        overtime_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(overtime_justifications)")
        }
        if "treatment" not in overtime_columns:
            con.execute("ALTER TABLE overtime_justifications ADD COLUMN treatment TEXT")
        user_columns = {row["name"] for row in con.execute("PRAGMA table_info(users)")}
        if "session_version" not in user_columns:
            con.execute(
                "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1"
            )
        if "admission_date" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN admission_date TEXT")
        if "position" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN position TEXT NOT NULL DEFAULT ''")
        punch_columns = {row["name"] for row in con.execute("PRAGMA table_info(punches)")}
        if "latitude" not in punch_columns:
            con.execute("ALTER TABLE punches ADD COLUMN latitude REAL")
        if "longitude" not in punch_columns:
            con.execute("ALTER TABLE punches ADD COLUMN longitude REAL")
        if "location_accuracy" not in punch_columns:
            con.execute("ALTER TABLE punches ADD COLUMN location_accuracy REAL")
        if "location_status" not in punch_columns:
            con.execute(
                "ALTER TABLE punches ADD COLUMN location_status TEXT NOT NULL DEFAULT 'NOT_COLLECTED'"
            )
        if "geofence_status" not in punch_columns:
            con.execute(
                "ALTER TABLE punches ADD COLUMN geofence_status TEXT NOT NULL DEFAULT 'NOT_EVALUATED'"
            )
        if "geofence_distance_meters" not in punch_columns:
            con.execute("ALTER TABLE punches ADD COLUMN geofence_distance_meters REAL")
        if "geofence_reference" not in punch_columns:
            con.execute("ALTER TABLE punches ADD COLUMN geofence_reference TEXT")
        if "geofence_reason" not in punch_columns:
            con.execute("ALTER TABLE punches ADD COLUMN geofence_reason TEXT")
        settings_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(company_settings)")
        }
        if "geofence_enabled" not in settings_columns:
            con.execute(
                "ALTER TABLE company_settings ADD COLUMN geofence_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "geofence_latitude" not in settings_columns:
            con.execute("ALTER TABLE company_settings ADD COLUMN geofence_latitude REAL")
        if "geofence_longitude" not in settings_columns:
            con.execute("ALTER TABLE company_settings ADD COLUMN geofence_longitude REAL")
        if "geofence_radius_meters" not in settings_columns:
            con.execute(
                "ALTER TABLE company_settings ADD COLUMN geofence_radius_meters INTEGER NOT NULL DEFAULT 200"
            )
        if "geofence_label" not in settings_columns:
            con.execute(
                "ALTER TABLE company_settings ADD COLUMN geofence_label TEXT NOT NULL DEFAULT 'Local principal'"
            )
        con.execute(
            """INSERT OR IGNORE INTO company_settings(
               id,company_name,company_document,work_start,lunch_start,lunch_end,
               work_end,workday_minutes,tolerance_minutes,updated_at)
               VALUES(1,'Empresa Demonstração','','08:00','12:00','13:30','18:00',510,10,?)""",
            (now_local().isoformat(),),
        )
        con.execute(
            """UPDATE users SET admission_date=substr(created_at,1,10)
               WHERE admission_date IS NULL OR admission_date=''"""
        )
        if not con.execute("SELECT 1 FROM users WHERE role='MANAGER'").fetchone():
            stamp = now_local().isoformat()
            con.execute(
                "INSERT INTO users(name,registration,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("Gestor do Sistema", "admin", password_hash("Admin@123"), "MANAGER", stamp),
            )
        if not con.execute("SELECT 1 FROM users WHERE registration='1001'").fetchone():
            stamp = now_local().isoformat()
            con.execute(
                "INSERT INTO users(name,registration,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("Funcionário Demonstração", "1001", password_hash("Teste@123"), "EMPLOYEE", stamp),
            )
        con.execute(
            """UPDATE users SET admission_date=substr(created_at,1,10)
               WHERE admission_date IS NULL OR admission_date=''"""
        )
        con.execute(
            """UPDATE users SET position='Colaborador'
               WHERE role='EMPLOYEE' AND (position IS NULL OR position='')"""
        )


def rows_as_dict(rows):
    return [dict(row) for row in rows]


def get_settings(con):
    row = con.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
    return dict(row) if row else {
        "company_name": "Empresa Demonstração",
        "company_document": "",
        "work_start": "08:00",
        "lunch_start": "12:00",
        "lunch_end": "13:30",
        "work_end": "18:00",
        "workday_minutes": WORKDAY_MINUTES,
        "tolerance_minutes": TOLERANCE_MINUTES,
        "geofence_enabled": 0,
        "geofence_latitude": None,
        "geofence_longitude": None,
        "geofence_radius_meters": 200,
        "geofence_label": "Local principal",
    }


def parse_iso(value):
    return datetime.fromisoformat(value)


def haversine_meters(lat1, lon1, lat2, lon2):
    radius = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def evaluate_geofence(settings, punch_type, latitude, longitude, location_status):
    active_types = {"ENTRADA", "SAIDA"}
    label = settings.get("geofence_label") or "Local principal"
    if punch_type not in active_types:
        return {"status": "INACTIVE_FOR_PUNCH", "distance": None, "reference": label}
    if not settings.get("geofence_enabled"):
        return {"status": "DISABLED", "distance": None, "reference": label}
    reference_latitude = settings.get("geofence_latitude")
    reference_longitude = settings.get("geofence_longitude")
    radius = settings.get("geofence_radius_meters") or 0
    if reference_latitude is None or reference_longitude is None or radius <= 0:
        return {"status": "NOT_CONFIGURED", "distance": None, "reference": label}
    if location_status != "CAPTURED" or latitude is None or longitude is None:
        return {"status": "LOCATION_NOT_CAPTURED", "distance": None, "reference": label}
    distance = haversine_meters(
        latitude,
        longitude,
        float(reference_latitude),
        float(reference_longitude),
    )
    return {
        "status": "INSIDE" if distance <= int(radius) else "OUTSIDE",
        "distance": distance,
        "reference": label,
    }


def location_export_text(location):
    geofence = location.get("geofence_status", "NOT_EVALUATED")
    distance = location.get("geofence_distance_meters")
    reason = location.get("geofence_reason")
    parts = []
    if location["status"] == "CAPTURED":
        parts.append(
            f'{location["latitude"]:.6f},{location["longitude"]:.6f} '
            f'(precisao {location["accuracy"]:.0f}m)'
        )
    else:
        parts.append(location["status"])
    parts.append(f"Cerca: {geofence}")
    if distance is not None:
        parts.append(f"Distancia: {distance:.0f}m")
    if reason:
        parts.append(f"Justificativa: {reason}")
    return " - ".join(parts)


def effective_punches(con, user_id, date_from=None, date_to=None):
    params = [user_id]
    where = ["p.user_id=?"]
    if date_from:
        where.append("date(p.punched_at)>=?")
        params.append(date_from)
    if date_to:
        where.append("date(p.punched_at)<=?")
        params.append(date_to)
    originals = rows_as_dict(
        con.execute(
            f"""SELECT p.*, u.name, u.registration
                FROM punches p JOIN users u ON u.id=p.user_id
                WHERE {' AND '.join(where)} ORDER BY p.punched_at""",
            params,
        )
    )
    approved = rows_as_dict(
        con.execute(
            """SELECT c.*, u.name, u.registration
               FROM corrections c JOIN users u ON u.id=c.user_id
               WHERE c.user_id=? AND c.status='APPROVED'
               ORDER BY c.requested_at_value""",
            (user_id,),
        )
    )
    by_id = {p["id"]: p for p in originals}
    result = list(originals)
    for correction in approved:
        target = by_id.get(correction["punch_id"])
        if correction["action"] == "DELETE" and target in result:
            result.remove(target)
        elif correction["action"] == "EDIT" and target in result:
            replacement = dict(target)
            replacement["punched_at"] = correction["requested_at_value"]
            replacement["punch_type"] = correction["requested_type"] or target["punch_type"]
            replacement["corrected"] = True
            replacement["correction_id"] = correction["id"]
            result[result.index(target)] = replacement
        elif correction["action"] == "ADD":
            value = correction["requested_at_value"]
            if value and (not date_from or value[:10] >= date_from) and (not date_to or value[:10] <= date_to):
                result.append(
                    {
                        "id": None,
                        "user_id": user_id,
                        "punched_at": value,
                        "punch_type": correction["requested_type"],
                        "source": "CORRECTION",
                        "name": correction["name"],
                        "registration": correction["registration"],
                        "corrected": True,
                        "correction_id": correction["id"],
                    }
                )
    return sorted(result, key=lambda item: item["punched_at"])


def day_summary(punches, settings=None):
    settings = settings or {
        "workday_minutes": WORKDAY_MINUTES,
        "tolerance_minutes": TOLERANCE_MINUTES,
    }
    ordered = sorted(punches, key=lambda item: item["punched_at"])
    workday_target = int(settings["workday_minutes"])
    if (
        ordered
        and parse_iso(ordered[0]["punched_at"]).date().weekday() >= 5
        and not all(item.get("source") == "DEMO" for item in ordered)
    ):
        workday_target = 0
    worked = 0
    for index in range(0, len(ordered) - 1, 2):
        start = parse_iso(ordered[index]["punched_at"])
        end = parse_iso(ordered[index + 1]["punched_at"])
        if end > start:
            worked += int((end - start).total_seconds() // 60)
    difference = worked - workday_target
    tolerance = int(settings["tolerance_minutes"])
    overtime = difference if difference > tolerance else 0
    undertime = abs(difference) if workday_target and difference < -tolerance and len(ordered) >= 4 else 0
    if not ordered:
        state = "SEM_REGISTROS"
    elif len(ordered) % 2 or len(ordered) < 4:
        state = "INCOMPLETO"
    elif overtime:
        state = "HORA_EXTRA"
    elif undertime:
        state = "CARGA_INFERIOR"
    else:
        state = "REGULAR"
    return {
        "worked_minutes": worked,
        "overtime_minutes": overtime,
        "undertime_minutes": undertime,
        "state": state,
        "punch_count": len(ordered),
    }


def xlsx_bytes(headers, data):
    def esc(value):
        return (
            str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    rows = [headers] + data
    sheet_rows = []
    for row_num, row in enumerate(rows, 1):
        cells = []
        for col_num, value in enumerate(row, 1):
            col = ""
            number = col_num
            while number:
                number, rem = divmod(number - 1, 26)
                col = chr(65 + rem) + col
            cells.append(
                f'<c r="{col}{row_num}" t="inlineStr"><is><t>{esc(value if value is not None else "")}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_num}">{"".join(cells)}</row>')
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Registros" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>""",
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "PontoAcademico/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 70_000_000:
            raise ValueError("Conteúdo muito grande.")
        return json.loads(self.rfile.read(length) or b"{}")

    def session_user(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie"))
        morsel = jar.get("ponto_session")
        if not morsel:
            return None
        with SESSION_LOCK:
            session = SESSIONS.get(morsel.value)
        if not session or session["expires"] < now_local():
            return None
        with db() as con:
            row = con.execute(
                "SELECT id,name,registration,role,session_version FROM users WHERE id=? AND active=1",
                (session["user_id"],),
            ).fetchone()
        if not row or row["session_version"] != session.get("session_version"):
            with SESSION_LOCK:
                SESSIONS.pop(morsel.value, None)
            return None
        result = dict(row)
        result.pop("session_version", None)
        return result

    def require_user(self, role=None):
        user = self.session_user()
        if not user:
            self.send_json(401, {"error": "Sessão expirada. Entre novamente."})
            return None
        if role and user["role"] != role:
            self.send_json(403, {"error": "Você não tem permissão para esta operação."})
            return None
        return user

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self.api_get(parsed)
        path = parsed.path
        if path in ("/", "/login"):
            path = "/index.html"
        file_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR.resolve() not in file_path.parents or not file_path.is_file():
            file_path = STATIC_DIR / "index.html"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file_path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return self.send_json(404, {"error": "Rota não encontrada."})
        try:
            payload = self.read_json()
            self.api_post(parsed.path, payload)
        except (json.JSONDecodeError, ValueError) as exc:
            self.send_json(400, {"error": str(exc) or "Dados inválidos."})
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "Registro duplicado ou dados incompatíveis."})
        except Exception as exc:
            print("Erro:", repr(exc))
            self.send_json(500, {"error": "Erro interno do servidor."})

    def api_get(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        if path == "/api/me":
            user = self.session_user()
            return self.send_json(200, {"user": user})
        if path == "/api/dashboard":
            user = self.require_user("EMPLOYEE")
            if not user:
                return
            today = now_local().date().isoformat()
            with db() as con:
                settings = get_settings(con)
                punches = effective_punches(con, user["id"], today, today)
                summary = day_summary(punches, settings)
                justification = con.execute(
                    "SELECT * FROM overtime_justifications WHERE user_id=? AND work_date=?",
                    (user["id"], today),
                ).fetchone()
                pending = rows_as_dict(
                    con.execute(
                        "SELECT * FROM corrections WHERE user_id=? ORDER BY requested_at DESC LIMIT 20",
                        (user["id"],),
                    )
                )
            next_type = PUNCH_TYPES[len(punches) % len(PUNCH_TYPES)]
            return self.send_json(
                200,
                {
                    "date": today,
                    "server_time": now_local().isoformat(),
                    "punches": punches,
                    "summary": summary,
                    "next_type": next_type,
                    "justification": dict(justification) if justification else None,
                    "corrections": pending,
                    "settings": settings,
                },
            )
        if path == "/api/employee/history":
            user = self.require_user("EMPLOYEE")
            if not user:
                return
            month = query.get("month", [now_local().strftime("%Y-%m")])[0]
            try:
                year, month_number = (int(part) for part in month.split("-"))
                first_day = date(year, month_number, 1)
            except (ValueError, TypeError):
                raise ValueError("Mês inválido.")
            last_day = date(
                year, month_number, calendar.monthrange(year, month_number)[1]
            )
            if first_day > now_local().date():
                raise ValueError("Não é possível consultar um mês futuro.")
            last_day = min(last_day, now_local().date())
            with db() as con:
                employee = dict(
                    con.execute(
                        """SELECT id,name,registration,created_at,admission_date,position
                           FROM users WHERE id=? AND role='EMPLOYEE'""",
                        (user["id"],),
                    ).fetchone()
                )
                history = build_report(
                    con,
                    [employee],
                    first_day.isoformat(),
                    last_day.isoformat(),
                )
            return self.send_json(
                200,
                {
                    "month": month,
                    "history": history,
                    "pending_overtime": [
                        item
                        for item in history
                        if item["overtime_minutes"] > 0
                        and not item["overtime_reason"]
                    ],
                },
            )
        if path == "/api/manager/settings":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            with db() as con:
                settings = get_settings(con)
            return self.send_json(200, {"settings": settings})
        if path == "/api/manager/audit":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            conditions = ["1=1"]
            params = []
            action = query.get("action", [""])[0].strip()
            actor_id = query.get("actor_id", [""])[0].strip()
            date_from = query.get("from", [""])[0].strip()
            date_to = query.get("to", [""])[0].strip()
            if action:
                conditions.append("a.action=?")
                params.append(action)
            if actor_id:
                conditions.append("a.actor_id=?")
                params.append(int(actor_id))
            if date_from:
                date.fromisoformat(date_from)
                conditions.append("date(a.created_at)>=?")
                params.append(date_from)
            if date_to:
                date.fromisoformat(date_to)
                conditions.append("date(a.created_at)<=?")
                params.append(date_to)
            with db() as con:
                entries = rows_as_dict(
                    con.execute(
                        f"""SELECT a.id,a.action,a.entity,a.entity_id,a.details,a.created_at,
                                   u.name actor_name,u.registration actor_registration
                            FROM audit_log a
                            LEFT JOIN users u ON u.id=a.actor_id
                            WHERE {' AND '.join(conditions)}
                            ORDER BY a.created_at DESC LIMIT 500""",
                        params,
                    )
                )
                actions = [
                    row["action"]
                    for row in con.execute(
                        "SELECT DISTINCT action FROM audit_log ORDER BY action"
                    )
                ]
            return self.send_json(
                200, {"entries": entries, "actions": actions}
            )
        if path == "/api/manager/backup":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            temporary = BASE_DIR / f".backup-{secrets.token_hex(8)}.db"
            try:
                source = db()
                destination = sqlite3.connect(temporary)
                source.backup(destination)
                destination.close()
                source.close()
                content = temporary.read_bytes()
            finally:
                if temporary.exists():
                    temporary.unlink()
            filename = f"ponto-backup-{now_local().strftime('%Y%m%d-%H%M%S')}.db"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.sqlite3")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            return self.wfile.write(content)
        if path == "/api/manager/users":
            user = self.require_user("MANAGER")
            if not user:
                return
            with db() as con:
                users = rows_as_dict(
                    con.execute(
                        "SELECT id,name,registration,active,created_at,admission_date,position FROM users WHERE role='EMPLOYEE' ORDER BY name"
                    )
                )
            return self.send_json(200, {"users": users})
        if path == "/api/manager/report":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            today = now_local().date()
            date_from = query.get("from", [(today - timedelta(days=30)).isoformat()])[0]
            date_to = query.get("to", [today.isoformat()])[0]
            selected = query.get("user_id", [""])[0]
            with db() as con:
                if selected:
                    employees = rows_as_dict(con.execute(
                        "SELECT id,name,registration,created_at,admission_date,position FROM users WHERE role='EMPLOYEE' AND id=?", (selected,)
                    ))
                else:
                    employees = rows_as_dict(con.execute(
                        "SELECT id,name,registration,created_at,admission_date,position FROM users WHERE role='EMPLOYEE' AND active=1 ORDER BY name"
                    ))
                report = build_report(con, employees, date_from, date_to)
                corrections = rows_as_dict(con.execute(
                    """SELECT c.*,u.name,u.registration,r.name reviewer_name
                       FROM corrections c JOIN users u ON u.id=c.user_id
                       LEFT JOIN users r ON r.id=c.reviewed_by
                       ORDER BY c.requested_at DESC LIMIT 100"""
                ))
            return self.send_json(200, {"report": report, "corrections": corrections})
        if path == "/api/manager/export":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            today = now_local().date()
            date_from = query.get("from", [(today - timedelta(days=30)).isoformat()])[0]
            date_to = query.get("to", [today.isoformat()])[0]
            selected = query.get("user_id", [""])[0]
            with db() as con:
                sql = "SELECT id,name,registration,created_at,admission_date,position FROM users WHERE role='EMPLOYEE'"
                params = []
                if selected:
                    sql += " AND id=?"
                    params.append(selected)
                report = build_report(con, rows_as_dict(con.execute(sql, params)), date_from, date_to)
            headers = ["Funcionário", "Matrícula", "Data", "Marcações", "Localizações", "Minutos trabalhados", "Hora extra", "Situação", "Justificativa", "Ocorrência"]
            data = [[
                r["name"], r["registration"], r["date"], " | ".join(r["times"]),
                " | ".join(
                    f'{location["latitude"]:.6f},{location["longitude"]:.6f} (±{location["accuracy"]:.0f}m)'
                    if location["status"] == "CAPTURED" else location["status"]
                    for location in r["locations"]
                ),
                r["worked_minutes"], r["overtime_minutes"], r["state"],
                r.get("overtime_reason", ""), r.get("day_note", "")
            ] for r in report]
            content = xlsx_bytes(headers, data)
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="registros-ponto.xlsx"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            return self.wfile.write(content)
        self.send_json(404, {"error": "Rota não encontrada."})

    def api_post(self, path, payload):
        if path == "/api/login":
            registration = str(payload.get("registration", "")).strip()
            password = str(payload.get("password", ""))
            with db() as con:
                row = con.execute("SELECT * FROM users WHERE registration=? AND active=1", (registration,)).fetchone()
            if not row or not password_ok(password, row["password_hash"]):
                return self.send_json(401, {"error": "Matrícula ou senha inválida."})
            token = secrets.token_urlsafe(32)
            with SESSION_LOCK:
                SESSIONS[token] = {
                    "user_id": row["id"],
                    "session_version": row["session_version"],
                    "expires": now_local() + timedelta(hours=8),
                }
            body = json.dumps({"user": {"id": row["id"], "name": row["name"], "registration": row["registration"], "role": row["role"]}}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", f"ponto_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if path == "/api/logout":
            jar = cookies.SimpleCookie(self.headers.get("Cookie"))
            if jar.get("ponto_session"):
                with SESSION_LOCK:
                    SESSIONS.pop(jar["ponto_session"].value, None)
            self.send_response(200)
            self.send_header("Set-Cookie", "ponto_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            self.send_header("Content-Length", "2")
            self.end_headers()
            return self.wfile.write(b"{}")
        if path == "/api/change-password":
            user = self.require_user("EMPLOYEE")
            if not user:
                return
            current_password = str(payload.get("current_password", ""))
            new_password = str(payload.get("new_password", ""))
            if len(new_password) < 8:
                raise ValueError("A nova senha deve possuir pelo menos 8 caracteres.")
            if current_password == new_password:
                raise ValueError("A nova senha deve ser diferente da senha atual.")
            stamp = now_local().isoformat()
            with db() as con:
                stored = con.execute(
                    "SELECT password_hash FROM users WHERE id=?", (user["id"],)
                ).fetchone()
                if not stored or not password_ok(
                    current_password, stored["password_hash"]
                ):
                    return self.send_json(401, {"error": "A senha atual está incorreta."})
                con.execute(
                    """UPDATE users
                       SET password_hash=?,session_version=session_version+1
                       WHERE id=?""",
                    (password_hash(new_password), user["id"]),
                )
                con.execute(
                    """INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        user["id"],
                        "CHANGE_PASSWORD",
                        "USER",
                        user["id"],
                        "Senha alterada pelo próprio funcionário",
                        stamp,
                    ),
                )
            return self.send_json(
                200,
                {
                    "message": "Senha alterada. Entre novamente com a nova senha."
                },
            )
        if path == "/api/punch":
            user = self.require_user("EMPLOYEE")
            if not user:
                return
            stamp = now_local()
            today = stamp.date().isoformat()
            location = payload.get("location") or {}
            location_status = str(location.get("status", "UNAVAILABLE")).upper()
            if location_status not in {"CAPTURED", "DENIED", "UNAVAILABLE", "TIMEOUT"}:
                location_status = "UNAVAILABLE"
            latitude = longitude = accuracy = None
            if location_status == "CAPTURED":
                try:
                    latitude = float(location["latitude"])
                    longitude = float(location["longitude"])
                    accuracy = float(location["accuracy"])
                    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                        raise ValueError
                    if accuracy < 0:
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise ValueError("Dados de localização inválidos.")
            with db() as con:
                punches = effective_punches(con, user["id"], today, today)
                if punches:
                    seconds = (stamp - parse_iso(punches[-1]["punched_at"])).total_seconds()
                    if seconds < 30 and not payload.get("confirm_close"):
                        return self.send_json(409, {"confirmation_required": True, "seconds": max(0, int(seconds)), "error": "Existe uma marcação feita há menos de 30 segundos. Deseja registrar novamente?"})
                punch_type = PUNCH_TYPES[len(punches) % len(PUNCH_TYPES)]
                settings = get_settings(con)
                geofence = evaluate_geofence(
                    settings,
                    punch_type,
                    latitude,
                    longitude,
                    location_status,
                )
                geofence_reason = str(payload.get("geofence_reason", "")).strip()
                if geofence["status"] == "OUTSIDE" and len(geofence_reason) < 5:
                    return self.send_json(
                        409,
                        {
                            "geofence_reason_required": True,
                            "distance_meters": round(geofence["distance"] or 0),
                            "radius_meters": int(settings.get("geofence_radius_meters") or 0),
                            "reference": geofence["reference"],
                            "punch_type": punch_type,
                            "error": (
                                "Voce esta fora da cerca geografica definida para "
                                "esta marcacao. Informe uma justificativa para registrar o ponto."
                            ),
                        },
                    )
                cursor = con.execute(
                    """INSERT INTO punches(
                       user_id,punched_at,punch_type,created_at,latitude,longitude,
                       location_accuracy,location_status,geofence_status,
                       geofence_distance_meters,geofence_reference,geofence_reason)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user["id"],
                        stamp.isoformat(),
                        punch_type,
                        stamp.isoformat(),
                        latitude,
                        longitude,
                        accuracy,
                        location_status,
                        geofence["status"],
                        geofence["distance"],
                        geofence["reference"],
                        geofence_reason or None,
                    ),
                )
                con.execute(
                    "INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        user["id"],
                        "CREATE",
                        "PUNCH",
                        cursor.lastrowid,
                        json.dumps(
                            {
                                "punch_type": punch_type,
                                "geofence_status": geofence["status"],
                                "distance_meters": round(geofence["distance"])
                                if geofence["distance"] is not None
                                else None,
                            },
                            ensure_ascii=False,
                        ),
                        stamp.isoformat(),
                    ),
                )
                updated = effective_punches(con, user["id"], today, today)
                summary = day_summary(updated, settings)
            return self.send_json(201, {"message": "Ponto registrado com sucesso.", "summary": summary, "punch_type": punch_type, "geofence": geofence})
        if path == "/api/overtime-justification":
            user = self.require_user("EMPLOYEE")
            if not user:
                return
            work_date = str(payload.get("work_date", ""))
            reason = str(payload.get("reason", "")).strip()
            if len(reason) < 5:
                raise ValueError("Informe uma justificativa com pelo menos 5 caracteres.")
            with db() as con:
                summary = day_summary(
                    effective_punches(con, user["id"], work_date, work_date),
                    get_settings(con),
                )
                if summary["overtime_minutes"] <= 0:
                    raise ValueError("Não há hora extra calculada nessa data.")
                stamp = now_local().isoformat()
                con.execute(
                    """INSERT INTO overtime_justifications(user_id,work_date,minutes,reason,created_at,updated_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(user_id,work_date) DO UPDATE SET
                       minutes=excluded.minutes,reason=excluded.reason,status='PENDING',
                       treatment=NULL,reviewed_by=NULL,reviewed_at=NULL,review_note=NULL,
                       updated_at=excluded.updated_at""",
                    (user["id"], work_date, summary["overtime_minutes"], reason, stamp, stamp),
                )
            return self.send_json(200, {"message": "Justificativa enviada ao gestor."})
        if path == "/api/corrections":
            user = self.require_user()
            if not user:
                return
            target_user_id = user["id"] if user["role"] == "EMPLOYEE" else int(payload.get("user_id") or 0)
            action = str(payload.get("action", "ADD")).upper()
            value = str(payload.get("requested_at_value", ""))
            reason = str(payload.get("reason", "")).strip()
            punch_type = str(payload.get("requested_type", ""))
            punch_id = payload.get("punch_id")
            if action not in ("ADD", "EDIT", "DELETE") or len(reason) < 5:
                raise ValueError("Informe a ação e uma justificativa válida.")
            if action in ("ADD", "EDIT"):
                parse_iso(value)
                if punch_type not in PUNCH_TYPES:
                    raise ValueError("Tipo de marcação inválido.")
            status = "PENDING" if user["role"] == "EMPLOYEE" else "APPROVED"
            stamp = now_local().isoformat()
            with db() as con:
                cursor = con.execute(
                    """INSERT INTO corrections(user_id,punch_id,action,requested_at_value,requested_type,reason,status,requested_by,reviewed_by,requested_at,reviewed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (target_user_id, punch_id, action, value or None, punch_type or None, reason, status, user["id"], user["id"] if status == "APPROVED" else None, stamp, stamp if status == "APPROVED" else None),
                )
                con.execute(
                    "INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                    (user["id"], "REQUEST" if status == "PENDING" else "CREATE_APPROVED", "CORRECTION", cursor.lastrowid, reason, stamp),
                )
            return self.send_json(201, {"message": "Solicitação enviada." if status == "PENDING" else "Correção registrada."})
        if path.startswith("/api/manager/corrections/"):
            manager = self.require_user("MANAGER")
            if not manager:
                return
            correction_id = int(path.rsplit("/", 1)[-1])
            status = str(payload.get("status", "")).upper()
            if status not in ("APPROVED", "REJECTED"):
                raise ValueError("Decisão inválida.")
            stamp = now_local().isoformat()
            with db() as con:
                con.execute(
                    "UPDATE corrections SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=? AND status='PENDING'",
                    (status, manager["id"], stamp, str(payload.get("note", "")).strip(), correction_id),
                )
                con.execute(
                    "INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                    (manager["id"], status, "CORRECTION", correction_id, str(payload.get("note", "")), stamp),
                )
            return self.send_json(200, {"message": "Solicitação analisada."})
        if path == "/api/manager/day-note":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            user_id = int(payload.get("user_id") or 0)
            work_date = str(payload.get("work_date", ""))
            category = str(payload.get("category", "")).strip()
            note = str(payload.get("note", "")).strip()
            date.fromisoformat(work_date)
            if not category or len(note) < 3:
                raise ValueError("Informe a categoria e a observação.")
            stamp = now_local().isoformat()
            with db() as con:
                con.execute(
                    """INSERT INTO day_notes(user_id,work_date,category,note,manager_id,created_at) VALUES(?,?,?,?,?,?)
                       ON CONFLICT(user_id,work_date) DO UPDATE SET category=excluded.category,note=excluded.note,manager_id=excluded.manager_id,created_at=excluded.created_at""",
                    (user_id, work_date, category, note, manager["id"], stamp),
                )
            return self.send_json(200, {"message": "Ocorrência registrada."})
        if path == "/api/manager/settings":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            company_name = str(payload.get("company_name", "")).strip()
            company_document = str(payload.get("company_document", "")).strip()
            schedule_fields = [
                str(payload.get(key, "")).strip()
                for key in ("work_start", "lunch_start", "lunch_end", "work_end")
            ]
            if len(company_name) < 2:
                raise ValueError("Informe o nome da empresa.")
            parsed_times = [time.fromisoformat(value) for value in schedule_fields]
            minutes_of_day = [value.hour * 60 + value.minute for value in parsed_times]
            if not (
                minutes_of_day[0]
                < minutes_of_day[1]
                < minutes_of_day[2]
                < minutes_of_day[3]
            ):
                raise ValueError("Os horários da jornada estão fora de sequência.")
            workday_minutes = (
                minutes_of_day[1]
                - minutes_of_day[0]
                + minutes_of_day[3]
                - minutes_of_day[2]
            )
            tolerance = int(payload.get("tolerance_minutes", 10))
            if tolerance < 0 or tolerance > 60:
                raise ValueError("A tolerância deve estar entre 0 e 60 minutos.")
            geofence_enabled = 1 if payload.get("geofence_enabled") else 0
            geofence_label = str(payload.get("geofence_label", "Local principal")).strip() or "Local principal"
            geofence_latitude = geofence_longitude = None
            geofence_radius = int(payload.get("geofence_radius_meters") or 200)
            if geofence_radius < 10 or geofence_radius > 10000:
                raise ValueError("O raio da cerca deve estar entre 10 e 10000 metros.")
            if geofence_enabled:
                try:
                    geofence_latitude = float(payload.get("geofence_latitude"))
                    geofence_longitude = float(payload.get("geofence_longitude"))
                    if not (
                        -90 <= geofence_latitude <= 90
                        and -180 <= geofence_longitude <= 180
                    ):
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError("Informe latitude e longitude validas para ativar a cerca.")
            stamp = now_local().isoformat()
            with db() as con:
                before = get_settings(con)
                con.execute(
                    """UPDATE company_settings SET
                       company_name=?,company_document=?,work_start=?,lunch_start=?,
                       lunch_end=?,work_end=?,workday_minutes=?,tolerance_minutes=?,
                       geofence_enabled=?,geofence_latitude=?,geofence_longitude=?,
                       geofence_radius_meters=?,geofence_label=?,updated_at=? WHERE id=1""",
                    (
                        company_name,
                        company_document,
                        *schedule_fields,
                        workday_minutes,
                        tolerance,
                        geofence_enabled,
                        geofence_latitude,
                        geofence_longitude,
                        geofence_radius,
                        geofence_label,
                        stamp,
                    ),
                )
                con.execute(
                    """INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        manager["id"],
                        "UPDATE_SETTINGS",
                        "COMPANY_SETTINGS",
                        1,
                        json.dumps(
                            {
                                "before": before,
                                "after": {
                                    "company_name": company_name,
                                    "company_document": company_document,
                                    "workday_minutes": workday_minutes,
                                    "tolerance_minutes": tolerance,
                                    "geofence_enabled": geofence_enabled,
                                    "geofence_radius_meters": geofence_radius,
                                    "geofence_label": geofence_label,
                                },
                            },
                            ensure_ascii=False,
                        ),
                        stamp,
                    ),
                )
            return self.send_json(
                200,
                {
                    "message": "Configurações atualizadas.",
                    "workday_minutes": workday_minutes,
                },
            )
        if path == "/api/manager/restore":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            encoded = str(payload.get("backup_base64", ""))
            if len(encoded) > 70_000_000:
                raise ValueError("O arquivo de backup é muito grande.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise ValueError("Arquivo de backup inválido.")
            if not content.startswith(b"SQLite format 3\x00"):
                raise ValueError("O arquivo selecionado não é um banco SQLite válido.")
            temporary = BASE_DIR / f".restore-{secrets.token_hex(8)}.db"
            temporary.write_bytes(content)
            try:
                source = sqlite3.connect(temporary)
                integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {
                    row[0]
                    for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                required = {"users", "punches", "audit_log", "company_settings"}
                if integrity != "ok" or not required.issubset(tables):
                    source.close()
                    raise ValueError("O backup está corrompido ou não pertence ao sistema.")
                destination = sqlite3.connect(DB_PATH)
                source.backup(destination)
                destination.close()
                source.close()
                with db() as con:
                    con.execute(
                        """INSERT INTO audit_log(actor_id,action,entity,details,created_at)
                           VALUES(NULL,'RESTORE_BACKUP','DATABASE',?,?)""",
                        ("Banco restaurado por um gestor autenticado", now_local().isoformat()),
                    )
                with SESSION_LOCK:
                    SESSIONS.clear()
            finally:
                if temporary.exists():
                    temporary.unlink()
            return self.send_json(
                200,
                {
                    "message": "Backup restaurado. Todas as sessões foram encerradas."
                },
            )
        if path == "/api/manager/overtime-review":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            user_id = int(payload.get("user_id") or 0)
            work_date = str(payload.get("work_date", ""))
            status = str(payload.get("status", "")).upper()
            treatment = str(payload.get("treatment", "")).upper()
            note = str(payload.get("note", "")).strip()
            date.fromisoformat(work_date)
            if status not in ("APPROVED", "REJECTED"):
                raise ValueError("Selecione a análise da justificativa.")
            if treatment not in ("PAYMENT", "TIME_BANK", "HR_REVIEW"):
                raise ValueError("Selecione o tratamento administrativo.")
            stamp = now_local().isoformat()
            with db() as con:
                justification = con.execute(
                    """SELECT id,minutes FROM overtime_justifications
                       WHERE user_id=? AND work_date=?""",
                    (user_id, work_date),
                ).fetchone()
                if not justification:
                    raise ValueError("O funcionário ainda não enviou a justificativa.")
                con.execute(
                    """UPDATE overtime_justifications
                       SET status=?,treatment=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=?
                       WHERE id=?""",
                    (
                        status,
                        treatment,
                        manager["id"],
                        stamp,
                        note,
                        stamp,
                        justification["id"],
                    ),
                )
                con.execute(
                    """INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        manager["id"],
                        "REVIEW_REASON",
                        "OVERTIME_JUSTIFICATION",
                        justification["id"],
                        json.dumps(
                            {
                                "status": status,
                                "treatment": treatment,
                                "minutes_preserved": justification["minutes"],
                                "note": note,
                            },
                            ensure_ascii=False,
                        ),
                        stamp,
                    ),
                )
            return self.send_json(
                200,
                {
                    "message": "Justificativa analisada. As horas apuradas foram preservadas."
                },
            )
        if path == "/api/manager/users":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            name = str(payload.get("name", "")).strip()
            registration = str(payload.get("registration", "")).strip()
            password = str(payload.get("password", ""))
            admission_date = str(payload.get("admission_date", "")).strip()
            position = str(payload.get("position", "")).strip()
            if len(name) < 3:
                raise ValueError("Informe o nome completo do funcionário.")
            if len(registration) < 2 or len(registration) > 30:
                raise ValueError("A matrícula deve possuir entre 2 e 30 caracteres.")
            if not all(character.isalnum() or character in "-_." for character in registration):
                raise ValueError("A matrícula deve conter apenas letras, números, ponto, hífen ou sublinhado.")
            if len(password) < 8:
                raise ValueError("A senha inicial deve possuir pelo menos 8 caracteres.")
            date.fromisoformat(admission_date)
            if len(position) < 2:
                raise ValueError("Informe o cargo do funcionário.")
            stamp = now_local().isoformat()
            with db() as con:
                cursor = con.execute(
                    """INSERT INTO users(name,registration,password_hash,role,active,created_at,admission_date,position)
                       VALUES(?,?,?,'EMPLOYEE',1,?,?,?)""",
                    (
                        name,
                        registration,
                        password_hash(password),
                        stamp,
                        admission_date,
                        position,
                    ),
                )
                con.execute(
                    """INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        manager["id"],
                        "CREATE",
                        "USER",
                        cursor.lastrowid,
                        json.dumps({"name": name, "registration": registration}, ensure_ascii=False),
                        stamp,
                    ),
                )
            return self.send_json(
                201,
                {
                    "message": "Funcionário cadastrado com sucesso.",
                    "user": {
                        "id": cursor.lastrowid,
                        "name": name,
                        "registration": registration,
                        "admission_date": admission_date,
                        "position": position,
                        "active": 1,
                    },
                },
            )
        if path == "/api/manager/demo-day":
            manager = self.require_user("MANAGER")
            if not manager:
                return
            user_id = int(payload.get("user_id") or 0)
            work_date = date.fromisoformat(str(payload.get("work_date", "")))
            scenario = str(payload.get("scenario", "")).upper()
            if scenario not in ("REGULAR", "OVERTIME", "INCOMPLETE"):
                raise ValueError("Selecione um cenário de demonstração válido.")
            if work_date > now_local().date():
                raise ValueError("A demonstração não pode utilizar uma data futura.")
            stamp = now_local().isoformat()
            date_text = work_date.isoformat()
            with db() as con:
                settings = get_settings(con)
                end_time = time.fromisoformat(settings["work_end"])
                overtime_end = (
                    datetime.combine(work_date, end_time) + timedelta(hours=1)
                ).time().strftime("%H:%M:%S")
                regular = [
                    (settings["work_start"], "ENTRADA"),
                    (settings["lunch_start"], "SAIDA_ALMOCO"),
                    (settings["lunch_end"], "RETORNO_ALMOCO"),
                    (settings["work_end"], "SAIDA"),
                ]
                scenarios = {
                    "REGULAR": regular,
                    "OVERTIME": regular[:-1] + [(overtime_end, "SAIDA")],
                    "INCOMPLETE": regular[:-1],
                }
                employee = con.execute(
                    "SELECT id,created_at,admission_date FROM users WHERE id=? AND role='EMPLOYEE' AND active=1",
                    (user_id,),
                ).fetchone()
                if not employee:
                    raise ValueError("Funcionário não encontrado ou inativo.")
                admission_date = date.fromisoformat(
                    employee["admission_date"] or employee["created_at"][:10]
                )
                account_created_date = parse_iso(employee["created_at"]).date()
                if work_date < max(admission_date, account_created_date):
                    raise ValueError(
                        "A demonstração não pode utilizar data anterior à admissão ou à criação da conta."
                    )
                existing = effective_punches(con, user_id, date_text, date_text)
                if existing:
                    raise ValueError("Esse funcionário já possui marcações nessa data. Escolha outro dia.")
                for clock, punch_type in scenarios[scenario]:
                    punched_at = datetime.combine(
                        work_date, time.fromisoformat(clock), tzinfo=now_local().tzinfo
                    ).isoformat()
                    cursor = con.execute(
                        """INSERT INTO punches(user_id,punched_at,punch_type,source,created_at)
                           VALUES(?,?,?,'DEMO',?)""",
                        (user_id, punched_at, punch_type, stamp),
                    )
                    con.execute(
                        """INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (
                            manager["id"],
                            "CREATE_DEMO",
                            "PUNCH",
                            cursor.lastrowid,
                            json.dumps(
                                {"scenario": scenario, "work_date": date_text},
                                ensure_ascii=False,
                            ),
                            stamp,
                        ),
                    )
                summary = day_summary(
                    effective_punches(con, user_id, date_text, date_text),
                    get_settings(con),
                )
            return self.send_json(
                201,
                {
                    "message": "Jornada de demonstração criada.",
                    "summary": summary,
                },
            )
        if path.startswith("/api/manager/users/"):
            manager = self.require_user("MANAGER")
            if not manager:
                return
            parts = path.strip("/").split("/")
            if len(parts) != 5:
                return self.send_json(404, {"error": "Rota não encontrada."})
            user_id = int(parts[3])
            operation = parts[4]
            stamp = now_local().isoformat()
            with db() as con:
                employee = con.execute(
                    "SELECT id,name,registration,active,admission_date,position FROM users WHERE id=? AND role='EMPLOYEE'",
                    (user_id,),
                ).fetchone()
                if not employee:
                    raise ValueError("Funcionário não encontrado.")
                if operation == "update":
                    name = str(payload.get("name", "")).strip()
                    registration = str(payload.get("registration", "")).strip()
                    admission_date = str(payload.get("admission_date", "")).strip()
                    position = str(payload.get("position", "")).strip()
                    if len(name) < 3:
                        raise ValueError("Informe o nome completo do funcionário.")
                    if len(registration) < 2 or len(registration) > 30:
                        raise ValueError("A matrícula deve possuir entre 2 e 30 caracteres.")
                    if not all(character.isalnum() or character in "-_." for character in registration):
                        raise ValueError("A matrícula contém caracteres inválidos.")
                    date.fromisoformat(admission_date)
                    if len(position) < 2:
                        raise ValueError("Informe o cargo do funcionário.")
                    before = {
                        "name": employee["name"],
                        "registration": employee["registration"],
                        "admission_date": employee["admission_date"],
                        "position": employee["position"],
                    }
                    con.execute(
                        """UPDATE users SET name=?,registration=?,admission_date=?,position=?
                           WHERE id=?""",
                        (name, registration, admission_date, position, user_id),
                    )
                    details = {
                        "before": before,
                        "after": {
                            "name": name,
                            "registration": registration,
                            "admission_date": admission_date,
                            "position": position,
                        },
                    }
                    action = "UPDATE"
                    message = "Dados do funcionário atualizados."
                elif operation == "reset-password":
                    password = str(payload.get("password", ""))
                    if len(password) < 8:
                        raise ValueError("A nova senha deve possuir pelo menos 8 caracteres.")
                    con.execute(
                        """UPDATE users
                           SET password_hash=?,session_version=session_version+1
                           WHERE id=?""",
                        (password_hash(password), user_id),
                    )
                    details = {"registration": employee["registration"]}
                    action = "RESET_PASSWORD"
                    message = "Senha redefinida com sucesso."
                elif operation == "status":
                    active = 1 if bool(payload.get("active")) else 0
                    con.execute(
                        """UPDATE users
                           SET active=?,session_version=session_version+1
                           WHERE id=?""",
                        (active, user_id),
                    )
                    details = {
                        "registration": employee["registration"],
                        "before": employee["active"],
                        "after": active,
                    }
                    action = "ACTIVATE" if active else "DEACTIVATE"
                    message = "Funcionário ativado." if active else "Funcionário desativado."
                else:
                    return self.send_json(404, {"error": "Operação não encontrada."})
                con.execute(
                    """INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        manager["id"],
                        action,
                        "USER",
                        user_id,
                        json.dumps(details, ensure_ascii=False),
                        stamp,
                    ),
                )
            return self.send_json(200, {"message": message})
        self.send_json(404, {"error": "Rota não encontrada."})


def build_report(con, employees, date_from, date_to):
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start or (end - start).days > 366:
        raise ValueError("Período inválido ou superior a 366 dias.")
    result = []
    for employee in employees:
        account_created_date = (
            parse_iso(employee["created_at"]).date()
            if employee.get("created_at")
            else start
        )
        admission_date = (
            date.fromisoformat(employee["admission_date"])
            if employee.get("admission_date")
            else account_created_date
        )
        registration_date = max(account_created_date, admission_date)
        punches = effective_punches(con, employee["id"], date_from, date_to)
        by_day = {}
        for punch in punches:
            by_day.setdefault(punch["punched_at"][:10], []).append(punch)
        current = start
        while current <= end:
            key = current.isoformat()
            items = by_day.get(key, [])
            is_registered = current >= registration_date
            if (
                is_registered
                and (current.weekday() < 5 or items)
                and current <= now_local().date()
            ):
                summary = day_summary(items, get_settings(con))
                justification = con.execute(
                    "SELECT reason,status,treatment,review_note FROM overtime_justifications WHERE user_id=? AND work_date=?",
                    (employee["id"], key),
                ).fetchone()
                note = con.execute(
                    "SELECT category,note FROM day_notes WHERE user_id=? AND work_date=?",
                    (employee["id"], key),
                ).fetchone()
                result.append(
                    {
                        "user_id": employee["id"],
                        "name": employee["name"],
                        "registration": employee["registration"],
                        "date": key,
                        "times": [parse_iso(p["punched_at"]).strftime("%H:%M:%S") for p in items],
                        "locations": [
                            {
                                "status": p.get("location_status", "NOT_COLLECTED"),
                                "latitude": p.get("latitude"),
                                "longitude": p.get("longitude"),
                                "accuracy": p.get("location_accuracy"),
                                "geofence_status": p.get("geofence_status", "NOT_EVALUATED"),
                                "geofence_distance_meters": p.get("geofence_distance_meters"),
                                "geofence_reference": p.get("geofence_reference"),
                                "geofence_reason": p.get("geofence_reason"),
                            }
                            for p in items
                        ],
                        **summary,
                        "overtime_reason": justification["reason"] if justification else "",
                        "overtime_status": justification["status"] if justification else "",
                        "overtime_treatment": justification["treatment"] if justification else "",
                        "overtime_review_note": justification["review_note"] if justification else "",
                        "day_note": f'{note["category"]}: {note["note"]}' if note else "",
                    }
                )
            current += timedelta(days=1)
    return sorted(result, key=lambda item: (item["date"], item["name"]), reverse=True)


if __name__ == "__main__":
    init_db()
    print(f"\nSistema de Ponto disponível em http://{HOST}:{PORT}")
    print("Para encerrar, pressione Ctrl+C.\n")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
