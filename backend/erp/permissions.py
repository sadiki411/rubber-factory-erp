from rest_framework.permissions import BasePermission, SAFE_METHODS


READ_ONLY_GROUP_NAME = "GPT只读测试"


def is_read_only_user(user) -> bool:
    """Return whether an authenticated user belongs to the ERP read-only group."""

    return bool(
        user
        and user.is_authenticated
        and user.groups.filter(name=READ_ONLY_GROUP_NAME).exists()
    )


class IsAuthenticatedWithReadOnlyGuard(BasePermission):
    """Allow normal authenticated users to write, but keep test accounts read-only."""

    message = "此账号为只读测试账号，只能查看数据，不能新增、修改或删除。"

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return not is_read_only_user(user)
