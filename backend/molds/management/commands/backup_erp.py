import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone


BACKUP_PREFIX = "mold-erp-backup-"


def _validate_archive_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise CommandError(f"备份包包含不安全路径：{name}")


def _integrity_check(database_path):
    connection = sqlite3.connect(str(database_path))
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not result or result[0] != "ok":
        raise CommandError(f"SQLite完整性检查失败：{result[0] if result else '无结果'}")


class Command(BaseCommand):
    help = "在线一致性备份SQLite和媒体文件；也可显式恢复已生成的备份包。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=os.getenv("BACKUP_DIR", str(Path(settings.BASE_DIR).parent / "runtime" / "backups")),
            help="备份输出目录。",
        )
        parser.add_argument(
            "--retention-count",
            type=int,
            default=int(os.getenv("BACKUP_RETENTION_COUNT", "30")),
            help="保留最近多少份备份，默认30。",
        )
        parser.add_argument(
            "--retention-days",
            type=int,
            default=None,
            help="兼容旧部署参数：同时删除早于指定天数的备份。",
        )
        parser.add_argument(
            "--restore",
            metavar="ARCHIVE",
            help="从指定.zip备份包恢复数据库和媒体文件。",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="确认执行恢复；恢复会替换当前数据库和媒体目录。",
        )

    def handle(self, *args, **options):
        if options["retention_count"] < 1:
            raise CommandError("--retention-count必须大于0。")
        if options["retention_days"] is not None and options["retention_days"] < 1:
            raise CommandError("--retention-days必须大于0。")
        if options["restore"]:
            return self._restore(Path(options["restore"]), options["force"])
        return self._backup(
            Path(options["output"]),
            options["retention_count"],
            options["retention_days"],
        )

    def _backup(self, output_dir, retention_count, retention_days):
        database_path = Path(connections["default"].settings_dict["NAME"])
        if connections["default"].vendor != "sqlite":
            raise CommandError("backup_erp当前仅支持SQLite。")
        if not database_path.exists():
            raise CommandError(f"SQLite数据库不存在：{database_path}")

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S-%f")
        final_path = output_dir / f"{BACKUP_PREFIX}{timestamp}.zip"
        temporary_archive = output_dir / f".{final_path.name}.tmp"
        media_root = Path(settings.MEDIA_ROOT)

        with tempfile.TemporaryDirectory(prefix="mold-erp-backup-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            database_copy = temp_dir / "db.sqlite3"
            source = sqlite3.connect(
                f"file:{database_path.as_posix()}?mode=ro",
                uri=True,
                timeout=20,
            )
            destination = sqlite3.connect(str(database_copy))
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            _integrity_check(database_copy)

            manifest = {
                "format": 1,
                "created_at": timezone.localtime().isoformat(),
                "database": "data/db.sqlite3",
                "media": "media/",
            }
            try:
                with zipfile.ZipFile(
                    temporary_archive,
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    archive.write(database_copy, "data/db.sqlite3")
                    archive.writestr(
                        "manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                    )
                    if media_root.exists():
                        for media_file in sorted(media_root.rglob("*")):
                            if media_file.is_file():
                                archive.write(
                                    media_file,
                                    (PurePosixPath("media") / media_file.relative_to(media_root).as_posix()).as_posix(),
                                )
                os.replace(temporary_archive, final_path)
            finally:
                temporary_archive.unlink(missing_ok=True)

        self._prune(output_dir, final_path, retention_count, retention_days)
        self.stdout.write(self.style.SUCCESS(f"备份完成：{final_path}"))
        return str(final_path)

    def _prune(self, output_dir, current_path, retention_count, retention_days):
        archives = sorted(
            output_dir.glob(f"{BACKUP_PREFIX}*.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        keep = set(archives[:retention_count])
        cutoff = None
        if retention_days is not None:
            cutoff = datetime.now().astimezone() - timedelta(days=retention_days)
        for archive in archives:
            modified = datetime.fromtimestamp(archive.stat().st_mtime).astimezone()
            if archive not in keep or (cutoff is not None and modified < cutoff):
                if archive != current_path:
                    archive.unlink(missing_ok=True)

    def _restore(self, archive_path, force):
        if not force:
            raise CommandError("恢复会覆盖当前数据，请同时提供--force。")
        if not archive_path.is_file():
            raise CommandError(f"找不到备份包：{archive_path}")
        if connections["default"].vendor != "sqlite":
            raise CommandError("backup_erp当前仅支持SQLite。")

        database_path = Path(connections["default"].settings_dict["NAME"])
        media_root = Path(settings.MEDIA_ROOT)
        with tempfile.TemporaryDirectory(prefix="mold-erp-restore-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for member in archive.infolist():
                        _validate_archive_member(member.filename)
                    if "data/db.sqlite3" not in archive.namelist():
                        raise CommandError("备份包缺少data/db.sqlite3。")
                    archive.extractall(temp_dir)
            except zipfile.BadZipFile as exc:
                raise CommandError("备份包不是有效的ZIP文件。") from exc

            restored_database = temp_dir / "data" / "db.sqlite3"
            _integrity_check(restored_database)
            restored_media = temp_dir / "media"

            connections.close_all()
            database_path.parent.mkdir(parents=True, exist_ok=True)
            staged_database = database_path.with_suffix(database_path.suffix + ".restore")
            shutil.copy2(restored_database, staged_database)
            os.replace(staged_database, database_path)
            for suffix in ("-wal", "-shm"):
                Path(str(database_path) + suffix).unlink(missing_ok=True)

            staged_media = media_root.with_name(media_root.name + ".restore")
            if staged_media.exists():
                shutil.rmtree(staged_media)
            staged_media.mkdir(parents=True, exist_ok=True)
            if restored_media.exists():
                shutil.copytree(restored_media, staged_media, dirs_exist_ok=True)
            old_media = media_root.with_name(media_root.name + ".before-restore")
            if old_media.exists():
                shutil.rmtree(old_media)
            if media_root.exists():
                os.replace(media_root, old_media)
            os.replace(staged_media, media_root)
            if old_media.exists():
                shutil.rmtree(old_media)

        self.stdout.write(self.style.SUCCESS(f"恢复完成：{archive_path}"))
