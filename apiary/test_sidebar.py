from django.contrib.auth.models import Group, User
from django.test import TestCase


class SidebarTests(TestCase):
    databases = "__all__"

    def test_demo_models_nest_under_data_models_and_open_on_their_page(self):
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        page = self.client.get("/admin/").content.decode()
        self.assertIn("Data Models", page)
        self.assertIn('href="/admin/demo/mortalitybill/"', page)
        self.assertIn('href="/admin/demo/location/"', page)
        self.assertIn("subnavigationOpen", page)  # Unfold's collapsible sub-navigation
        self.assertIn("subnavigationOpen': false", page)  # closed away from the models

        on_model = self.client.get("/admin/demo/location/").content.decode()
        self.assertIn("subnavigationOpen': true", on_model)

    def test_every_project_links_its_tables_and_changes(self):
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        page = self.client.get("/admin/").content.decode()
        for label in ("demo", "bom", "relec"):
            self.assertIn(f'href="/admin/apiary/change/tables/{label}/"', page)
            self.assertIn(f'href="/admin/apiary/change/?project__exact={label}"', page)

    def test_a_project_member_sees_only_that_projects_tables(self):
        user = User.objects.create_user("reader", is_staff=True)
        user.groups.add(Group.objects.create(name="bom-viewer"))
        self.client.force_login(user)

        page = self.client.get("/admin/").content.decode()
        self.assertIn('href="/admin/apiary/change/tables/bom/"', page)
        self.assertNotIn('href="/admin/apiary/change/tables/relec/"', page)
        self.assertNotIn("/admin/apiary/site/?project__exact=bom", page)  # Sites: superusers only
        self.assertEqual(self.client.get("/admin/apiary/change/tables/bom/").status_code, 200)
        self.assertEqual(self.client.get("/admin/apiary/change/tables/relec/").status_code, 403)

    def test_the_current_page_is_highlighted(self):
        # Function links were never marked active: Unfold compares the link first.
        import re

        self.client.force_login(User.objects.create_superuser("root", password="x"))
        page = self.client.get("/admin/auth/user/").content.decode()
        anchor = re.search(r'<a href="/admin/auth/user/"[^>]*>', page).group(0)
        self.assertIn("active", anchor)


class FilteredLinkTests(TestCase):
    """Each project's Changes, Sites and Snapshots link the same list, filtered: only the shown project's lights up."""
    databases = "__all__"

    def active(self, url):
        import re

        self.client.force_login(User.objects.get_or_create(username="root", defaults={"is_superuser": True, "is_staff": True})[0])
        page = self.client.get(url).content.decode()
        return {href for href, rest in re.findall(r'<a href="(/admin/apiary/(?:change|site|datasnapshot)/[^"]*)"([^>]*)>', page)
                if "active" in rest}

    def test_only_the_filtered_projects_link_is_active(self):
        self.assertEqual(self.active("/admin/apiary/change/?project__exact=bom"), {"/admin/apiary/change/?project__exact=bom"})
        self.assertEqual(self.active("/admin/apiary/site/?project__exact=relec"), {"/admin/apiary/site/?project__exact=relec"})
        self.assertEqual(self.active("/admin/apiary/datasnapshot/"), {"/admin/apiary/datasnapshot/"})
        self.assertEqual(self.active("/admin/apiary/change/tables/bom/"), {"/admin/apiary/change/tables/bom/"})

    def test_an_object_page_keeps_its_lists_project(self):
        from apiary.models import Change

        change = Change.objects.create(project="bom", kind="records", summary="x")
        url = f"/admin/apiary/change/{change.pk}/change/?_changelist_filters=project__exact%3Dbom"
        self.assertEqual(self.active(url), {"/admin/apiary/change/?project__exact=bom"})
