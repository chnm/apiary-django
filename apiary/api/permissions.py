from rest_framework import permissions


class ModelPermissions(permissions.DjangoModelPermissions):
    # DRF lets any authenticated user read; project data needs the view
    # permission that the <project>-viewer group grants.
    perms_map = {
        **permissions.DjangoModelPermissions.perms_map,
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'HEAD': ['%(app_label)s.view_%(model_name)s'],
    }
