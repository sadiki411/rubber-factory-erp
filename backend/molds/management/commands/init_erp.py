import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from molds.services import seed_default_racks
from production.services import seed_default_stations


class Command(BaseCommand):
    help = "幂等初始化J01-J07货架、默认1-6号生产机台和共享登录账号。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=os.getenv("DJANGO_SUPERUSER_USERNAME", "erpadmin"),
            help="共享账号用户名（默认读取DJANGO_SUPERUSER_USERNAME）。",
        )
        parser.add_argument(
            "--email",
            default=os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@example.com"),
            help="共享账号邮箱（默认读取DJANGO_SUPERUSER_EMAIL）。",
        )
        parser.add_argument(
            "--password",
            default=os.getenv("DJANGO_SUPERUSER_PASSWORD"),
            help="新账号密码；重置时需配合--reset-password。",
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="若账号已存在，使用--password或环境变量重置密码。",
        )
        parser.add_argument(
            "--no-user",
            action="store_true",
            help="只初始化货架和生产站位，不创建或更新共享账号。",
        )

    def handle(self, *args, **options):
        rack_warnings = seed_default_racks()
        self.stdout.write(self.style.SUCCESS("J01-J07货架初始化完成。"))
        for warning in rack_warnings:
            self.stdout.write(self.style.WARNING(f"货架布局升级警告：{warning}"))
        seed_default_stations()
        self.stdout.write(self.style.SUCCESS("默认1-6号生产机台初始化完成，扩展机台保持不变。"))

        # journal_mode is persistent; the connection signal also applies busy_timeout.
        if connection.vendor == "sqlite" and connection.settings_dict["NAME"] != ":memory:":
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")

        if options["no_user"]:
            return

        username = str(options["username"] or "").strip()
        email = str(options["email"] or "").strip()
        if not username:
            raise CommandError("共享账号用户名不能为空。")

        User = get_user_model()
        user = User.objects.filter(username=username).first()
        created = user is None
        generated_password = None
        if created:
            password = options["password"]
            if not password:
                generated_password = secrets.token_urlsafe(18)
                password = generated_password
            user = User(username=username, email=email)
            user.set_password(password)
        else:
            if options["reset_password"]:
                password = options["password"]
                if not password:
                    raise CommandError(
                        "重置密码必须提供--password或DJANGO_SUPERUSER_PASSWORD。"
                    )
                user.set_password(password)
            if email:
                user.email = email

        # The single shared account also administers standard data through Django admin.
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"共享账号 {username} 已创建。"))
            if generated_password:
                self.stdout.write(
                    self.style.WARNING(
                        f"未提供初始密码，已生成一次性显示密码：{generated_password}"
                    )
                )
        else:
            message = f"共享账号 {username} 已存在，权限设置已校准"
            if options["reset_password"]:
                message += "，密码已重置"
            self.stdout.write(self.style.SUCCESS(message + "。"))
