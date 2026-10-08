import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from erp.permissions import READ_ONLY_GROUP_NAME


class Command(BaseCommand):
    help = "创建或校准一个由服务端强制限制为只读的ERP测试账号。"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="gpt_readonly")
        parser.add_argument("--password", default=None)
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="账号已存在时重置密码；未传--password则自动生成。",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        username = str(options["username"] or "").strip()
        if not username:
            raise CommandError("用户名不能为空。")

        User = get_user_model()
        user = User.objects.filter(username=username).first()
        created = user is None
        password = options["password"]
        password_changed = created or options["reset_password"]
        if password_changed and not password:
            password = secrets.token_urlsafe(18)

        if created:
            user = User(username=username)
        if password_changed:
            user.set_password(password)

        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.save()

        group, _ = Group.objects.get_or_create(name=READ_ONLY_GROUP_NAME)
        user.groups.set([group])
        user.user_permissions.clear()

        action = "已创建" if created else "已校准"
        self.stdout.write(self.style.SUCCESS(f"只读账号 {username} {action}。"))
        if password_changed:
            self.stdout.write(self.style.WARNING(f"账号密码：{password}"))
        else:
            self.stdout.write("密码保持不变；如需重置请加 --reset-password。")
