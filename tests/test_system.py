import base64
import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
import server


class SystemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = Path(tempfile.gettempdir()) / f"ponto_tests_{uuid.uuid4().hex}.db"
        server.DB_PATH = cls.database
        server.SESSIONS.clear()
        server.init_db()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=3)

    def client(self):
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, client, path, payload=None):
        if payload is None:
            return json.loads(client.open(self.base + path).read())
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return json.loads(client.open(request).read())

    def login(self, registration, password):
        client = self.client()
        self.request(
            client,
            "/api/login",
            {"registration": registration, "password": password},
        )
        return client

    def create_employee(self, manager, suffix):
        registration = f"test-{suffix}-{uuid.uuid4().hex[:6]}"
        created = self.request(
            manager,
            "/api/manager/users",
            {
                "name": f"Funcionário Teste {suffix}",
                "registration": registration,
                "password": "Teste@123",
                "admission_date": (server.now_local().date() - timedelta(days=30)).isoformat(),
                "position": "Analista",
            },
        )
        return created["user"], registration

    def test_10_authentication_and_permissions(self):
        employee = self.login("1001", "Teste@123")
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request(employee, "/api/manager/users")
        self.assertEqual(denied.exception.code, 403)
        dashboard = self.request(employee, "/api/dashboard")
        self.assertEqual(dashboard["next_type"], "ENTRADA")

    def test_20_configurable_schedule_and_demo_calculation(self):
        manager = self.login("admin", "Admin@123")
        response = self.request(
            manager,
            "/api/manager/settings",
            {
                "company_name": "Empresa Acadêmica",
                "company_document": "00.000.000/0001-00",
                "work_start": "08:00",
                "lunch_start": "12:00",
                "lunch_end": "13:30",
                "work_end": "18:00",
                "tolerance_minutes": 10,
            },
        )
        self.assertEqual(response["workday_minutes"], 510)
        user, _ = self.create_employee(manager, "jornada")
        demo = self.request(
            manager,
            "/api/manager/demo-day",
            {
                "user_id": user["id"],
                "work_date": server.now_local().date().isoformat(),
                "scenario": "OVERTIME",
            },
        )
        self.assertEqual(demo["summary"]["overtime_minutes"], 60)

    def test_30_overtime_reason_review_preserves_minutes(self):
        manager = self.login("admin", "Admin@123")
        user, registration = self.create_employee(manager, "extra")
        work_date = server.now_local().date().isoformat()
        self.request(
            manager,
            "/api/manager/demo-day",
            {"user_id": user["id"], "work_date": work_date, "scenario": "OVERTIME"},
        )
        employee = self.login(registration, "Teste@123")
        self.request(
            employee,
            "/api/overtime-justification",
            {"work_date": work_date, "reason": "Atendimento emergencial"},
        )
        self.request(
            manager,
            "/api/manager/overtime-review",
            {
                "user_id": user["id"],
                "work_date": work_date,
                "status": "REJECTED",
                "treatment": "HR_REVIEW",
                "note": "Motivo contestado, horas preservadas",
            },
        )
        month = work_date[:7]
        history = self.request(employee, f"/api/employee/history?month={month}")
        row = next(item for item in history["history"] if item["date"] == work_date)
        self.assertEqual(row["overtime_minutes"], 60)
        self.assertEqual(row["overtime_status"], "REJECTED")

    def test_40_password_change_invalidates_sessions(self):
        manager = self.login("admin", "Admin@123")
        _, registration = self.create_employee(manager, "senha")
        first = self.login(registration, "Teste@123")
        second = self.login(registration, "Teste@123")
        self.request(
            first,
            "/api/change-password",
            {"current_password": "Teste@123", "new_password": "NovaSenha@1"},
        )
        for client in (first, second):
            with self.assertRaises(urllib.error.HTTPError) as denied:
                self.request(client, "/api/dashboard")
            self.assertEqual(denied.exception.code, 401)
        self.assertIsNotNone(self.request(self.login(registration, "NovaSenha@1"), "/api/dashboard"))

    def test_50_audit_filters(self):
        manager = self.login("admin", "Admin@123")
        query = urllib.parse.urlencode({"action": "UPDATE_SETTINGS"})
        audit = self.request(manager, f"/api/manager/audit?{query}")
        self.assertTrue(audit["entries"])
        self.assertTrue(all(item["action"] == "UPDATE_SETTINGS" for item in audit["entries"]))

    def test_60_flexible_geofence_requires_reason_only_for_entry_and_exit(self):
        manager = self.login("admin", "Admin@123")
        self.request(
            manager,
            "/api/manager/settings",
            {
                "company_name": "Empresa Acadêmica",
                "company_document": "00.000.000/0001-00",
                "work_start": "08:00",
                "lunch_start": "12:00",
                "lunch_end": "13:30",
                "work_end": "18:00",
                "tolerance_minutes": 10,
                "geofence_enabled": True,
                "geofence_label": "Matriz",
                "geofence_latitude": -23.55052,
                "geofence_longitude": -46.633308,
                "geofence_radius_meters": 100,
            },
        )
        _, registration = self.create_employee(manager, "cerca")
        employee = self.login(registration, "Teste@123")
        outside = {
            "status": "CAPTURED",
            "latitude": -23.56052,
            "longitude": -46.643308,
            "accuracy": 20,
        }
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request(employee, "/api/punch", {"location": outside})
        self.assertEqual(denied.exception.code, 409)
        registered = self.request(
            employee,
            "/api/punch",
            {"location": outside, "geofence_reason": "Atendimento externo"},
        )
        self.assertEqual(registered["geofence"]["status"], "OUTSIDE")

        user, lunch_registration = self.create_employee(manager, "cerca-almoco")
        entrada = server.now_local().replace(hour=8, minute=0, second=0, microsecond=0)
        with server.db() as con:
            con.execute(
                """INSERT INTO punches(user_id,punched_at,punch_type,created_at)
                   VALUES(?,?,?,?)""",
                (
                    user["id"],
                    entrada.isoformat(),
                    "ENTRADA",
                    server.now_local().isoformat(),
                ),
            )
        lunch_employee = self.login(lunch_registration, "Teste@123")
        lunch = self.request(lunch_employee, "/api/punch", {"location": outside})
        self.assertEqual(lunch["punch_type"], "SAIDA_ALMOCO")
        self.assertEqual(lunch["geofence"]["status"], "INACTIVE_FOR_PUNCH")

    def test_70_approved_correction_uses_sao_paulo_time_in_report(self):
        manager = self.login("admin", "Admin@123")
        user, _ = self.create_employee(manager, "correcao-fuso")
        work_date = server.now_local().date().isoformat()
        stamp = server.now_local().isoformat()
        with server.db() as con:
            con.execute(
                """INSERT INTO corrections(
                   user_id,action,requested_at_value,requested_type,reason,status,
                   requested_by,reviewed_by,requested_at,reviewed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    user["id"],
                    "ADD",
                    f"{work_date}T20:06:00+00:00",
                    "ENTRADA",
                    "Esqueci",
                    "APPROVED",
                    user["id"],
                    1,
                    stamp,
                    stamp,
                ),
            )
        report = self.request(
            manager,
            f"/api/manager/report?from={work_date}&to={work_date}&user_id={user['id']}",
        )
        self.assertEqual(report["report"][0]["times"], ["17:06:00"])

    def test_80_export_splits_punch_times_into_columns(self):
        manager = self.login("admin", "Admin@123")
        work_date = server.now_local().date().isoformat()
        xlsx = manager.open(
            f"{self.base}/api/manager/export?from={work_date}&to={work_date}"
        ).read()
        with zipfile.ZipFile(BytesIO(xlsx)) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for header in (
            "Entrada",
            "Saída almoço",
            "Retorno almoço",
            "Saída",
            "Marcações extras",
        ):
            self.assertIn(header, sheet)

    def test_85_employee_history_uses_local_date_for_late_punches(self):
        manager = self.login("admin", "Admin@123")
        user, registration = self.create_employee(manager, "historico-local")
        work_date = server.now_local().date().isoformat()
        stamp = server.now_local().isoformat()
        with server.db() as con:
            con.executemany(
                """INSERT INTO punches(user_id,punched_at,punch_type,created_at)
                   VALUES(?,?,?,?)""",
                [
                    (user["id"], f"{work_date}T08:00:00-03:00", "ENTRADA", stamp),
                    (user["id"], f"{work_date}T12:00:00-03:00", "SAIDA_ALMOCO", stamp),
                    (user["id"], f"{work_date}T13:26:00-03:00", "RETORNO_ALMOCO", stamp),
                    (user["id"], f"{work_date}T23:37:00-03:00", "SAIDA", stamp),
                ],
            )
        employee = self.login(registration, "Teste@123")
        history = self.request(employee, f"/api/employee/history?month={work_date[:7]}")
        row = next(item for item in history["history"] if item["date"] == work_date)
        self.assertEqual(
            row["times"],
            ["08:00:00", "12:00:00", "13:26:00", "23:37:00"],
        )

    def test_90_backup_and_restore(self):
        manager = self.login("admin", "Admin@123")
        backup = manager.open(self.base + "/api/manager/backup").read()
        self.assertTrue(backup.startswith(b"SQLite format 3\x00"))
        _, registration = self.create_employee(manager, "apos-backup")
        restored = self.request(
            manager,
            "/api/manager/restore",
            {"backup_base64": base64.b64encode(backup).decode()},
        )
        self.assertIn("restaurado", restored["message"].lower())
        with self.assertRaises(urllib.error.HTTPError):
            self.login(registration, "Teste@123")
        manager = self.login("admin", "Admin@123")
        audit = self.request(manager, "/api/manager/audit")
        self.assertTrue(any(item["action"] == "RESTORE_BACKUP" for item in audit["entries"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
