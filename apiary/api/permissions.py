from rest_framework import permissions

from apiary.projects import has_role

# The role each method needs in the view's project (apiary.projects).
NEEDED = {"GET": "viewer", "HEAD": "viewer", "OPTIONS": "viewer",
          "POST": "editor", "PUT": "editor", "PATCH": "editor", "DELETE": "admin"}


class ProjectRolePermission(permissions.BasePermission):
    """Project data follows the user's role there: viewers read, editors write, admins delete."""

    def has_permission(self, request, view):
        needed = NEEDED.get(request.method)
        return needed is not None and has_role(request.user, view.project, needed)
