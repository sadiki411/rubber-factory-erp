import json
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from molds.models import Rack
from production.models import ProductionStation


@contextmanager
def backup_database_path(path):
    """Point backup_erp at an isolated SQLite file without replacing the test DB."""
    connection = connections["default"]
    original_name = connection.settings_dict["NAME"]
    connection.settings_dict["NAME"] = str(path)
    try:
        yield
    finally:
        connection.settings_dict["NAME"] = original_name


def create_sqlite(path, value):
    database = sqlite3.connect(path)
    try:
        database.execute("CREATE TABLE backup_probe (value TEXT NOT NULL)")
        database.execute("INSERT INTO backup_probe(value) VALUES (?)", [value])
        database.commit()
    finally:
        database.close()


def read_sqlite_value(path):
    database = sqlite3.connect(path)
    try:
        return database.execute("SELECT value FROM backup_probe").fetchone()[0]
    finally:
        database.close()


class BackupCommandTests(SimpleTestCase):
    databases = {"default"}

    def test_backup_contains_consistent_sqlite_media_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database_path = root / "data" / "db.sqlite3"
            database_path.parent.mkdir()
            create_sqlite(database_path, "before-backup")
            media_root = root / "media"
            image_path = media_root / "molds" / "M-001" / "photo.jpg"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"test-image")
            output_dir = root / "backups"

            with backup_database_path(database_path), override_settings(MEDIA_ROOT=media_root):
                archive_name = call_command(
                    "backup_erp", output=str(output_dir), retention_count=30, verbosity=0
                )

            archive_path = Path(archive_name)
            self.assertTrue(archive_path.is_file())
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("data/db.sqlite3", archive.namelist())
                self.assertIn("manifest.json", archive.namelist())
                self.assertIn("media/molds/M-001/photo.jpg", archive.namelist())
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["format"], 1)
                extracted_db = root / "extracted.sqlite3"
                extracted_db.write_bytes(archive.read("data/db.sqlite3"))
            self.assertEqual(read_sqlite_value(extracted_db), "before-backup")

    def test_retention_keeps_only_latest_requested_count(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database_path = root / "db.sqlite3"
            create_sqlite(database_path, "retention")
            output_dir = root / "backups"
            media_root = root / "media"

            with backup_database_path(database_path), override_settings(MEDIA_ROOT=media_root):
                for _ in range(4):
                    call_command(
                        "backup_erp", output=str(output_dir), retention_count=2, verbosity=0
                    )
            self.assertEqual(len(list(output_dir.glob("mold-erp-backup-*.zip"))), 2)

    def test_restore_replaces_database_and_media_from_archive(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database_path = root / "db.sqlite3"
            create_sqlite(database_path, "archived-value")
            media_root = root / "media"
            media_root.mkdir()
            (media_root / "kept.txt").write_text("archived-media", encoding="utf-8")
            output_dir = root / "backups"

            with backup_database_path(database_path), override_settings(MEDIA_ROOT=media_root):
                archive_name = call_command(
                    "backup_erp", output=str(output_dir), retention_count=30, verbosity=0
                )

                database_path.unlink()
                create_sqlite(database_path, "newer-value")
                (media_root / "kept.txt").write_text("newer-media", encoding="utf-8")
                (media_root / "remove-me.txt").write_text("not archived", encoding="utf-8")

                with patch("molds.management.commands.backup_erp.connections.close_all"):
                    call_command(
                        "backup_erp",
                        restore=archive_name,
                        force=True,
                        retention_count=30,
                        verbosity=0,
                    )

            self.assertEqual(read_sqlite_value(database_path), "archived-value")
            self.assertEqual((media_root / "kept.txt").read_text(encoding="utf-8"), "archived-media")
            self.assertFalse((media_root / "remove-me.txt").exists())

    def test_restore_requires_explicit_force(self):
        with tempfile.TemporaryDirectory() as temp_name:
            archive = Path(temp_name) / "placeholder.zip"
            archive.write_bytes(b"not-used-without-force")
            with self.assertRaisesMessage(CommandError, "--force"):
                call_command("backup_erp", restore=str(archive), retention_count=30, verbosity=0)


class InitializationCommandTests(TransactionTestCase):
    def test_init_command_is_idempotent_and_creates_shared_superuser(self):
        call_command(
            "init_erp",
            username="erp-shared",
            email="erp@example.com",
            password="initial-password",
            verbosity=0,
        )
        call_command(
            "init_erp",
            username="erp-shared",
            email="new@example.com",
            password="ignored-without-reset",
            verbosity=0,
        )
        self.assertEqual(Rack.objects.count(), 7)
        self.assertEqual(ProductionStation.objects.count(), 6)
        user = get_user_model().objects.get(username="erp-shared")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(user.check_password("initial-password"))

    def test_init_command_can_reset_shared_password(self):
        call_command(
            "init_erp", username="erp-shared", password="old-password", verbosity=0
        )
        call_command(
            "init_erp",
            username="erp-shared",
            password="new-password",
            reset_password=True,
            verbosity=0,
        )
        self.assertTrue(
            get_user_model().objects.get(username="erp-shared").check_password("new-password")
        )
