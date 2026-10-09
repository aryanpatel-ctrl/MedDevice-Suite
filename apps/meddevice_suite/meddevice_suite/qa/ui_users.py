"""Browser tests as each job (URS-PLT-009), on a test site only.

The browser tests sign in as one user per job and use the desk as that person
would: every area, list and form of their work. These helpers make those users
and tell the browser what each one may reach. They refuse to run anywhere but a
test site; the users are made only by the Administrator who runs the tests, and
their password is made afresh on every run and returned to the test, never stored
in the code.
"""

import frappe
from frappe import _
from frappe.boot import get_sidebar_items
from frappe.desk.desktop import get_workspaces
from frappe.desk.doctype.desktop_icon.desktop_icon import get_desktop_icons
from frappe.utils.password import update_password

from meddevice_suite.setup.lookups import REVIEWED_CLOSED
from meddevice_suite.setup.roles import JOB_PROFILES, PERMISSIONS, fills_in_by_hand
from meddevice_suite.setup.site_flags import site_flag

DOMAIN = "ui-tests.example.com"


def email_for(profile: str) -> str:
	return f"{frappe.scrub(profile).replace('_', '.')}@{DOMAIN}"


def ensure_test_site() -> None:
	if not site_flag("allow_tests"):
		frappe.throw(_("Browser-test helpers run only on a test site."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def prepare_job_users() -> dict:
	"""One user per job, with a password made for this run: {"password": …, "users": {job: email}}."""
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(_("Browser-test users are made only by the Administrator."), frappe.PermissionError)
	password = frappe.generate_hash(length=24)
	users = {}
	for profile in JOB_PROFILES:
		email = email_for(profile)
		user = (
			frappe.get_doc("User", email)
			if frappe.db.exists("User", email)
			else frappe.new_doc("User", email=email, send_welcome_email=0)
		)
		# An earlier run's user is brought back to exactly its job, whatever happened to it since.
		if user.is_new() or not user.enabled or [row.role_profile for row in user.role_profiles] != [profile]:
			user.update(
				{
					"first_name": profile,
					"last_name": "(browser test)",
					"enabled": 1,
					"role_profiles": [{"role_profile": profile}],
				}
			)
			user.save(ignore_permissions=True)  # test-site set-up by the Administrator, checked above
		update_password(email, password)
		users[profile] = email
	return {"password": password, "users": users}


# Any signed-in user may ask: the answer describes only what the session user may already
# open, from Frappe's own permission checks, and only on a test site.
@frappe.whitelist()
def reachable() -> dict:
	"""What the session user's desk reaches.

	- areas: the workspaces the user's desk loads, opened at /desk/<area>;
	- lists: every record type the sidebars of the user's desk list (the desk icons the user
	  sees, and the workspaces), and every one in `create`;
	- reports: the query and script reports those sidebars list;
	- create: the record types whose new form the user may fill in, of their desk and of the
	  regulated scope (the permission matrix and this app's own record types; see
	  fillable_form);
	- closed_lookups: [record type, "Form.field"] lookups of the user's job left closed on
	  purpose (meddevice_suite.setup.lookups.REVIEWED_CLOSED), which the browser does not
	  search.
	"""
	ensure_test_site()
	areas = sorted(page["name"] for page in get_workspaces()["pages"])
	items = sidebar_items(areas)
	listed = {item["link_to"] for item in items if item["link_type"] == "DocType" and item["link_to"]}
	creatable = sorted(doctype for doctype in listed | scope_record_types() if fillable_form(doctype))
	profiles = frappe.get_all(
		"User Role Profile", filters={"parent": frappe.session.user}, pluck="role_profile"
	)
	return {
		"areas": areas,
		"lists": sorted(listed | set(creatable)),
		"reports": sorted(
			{
				item["link_to"]
				for item in items
				if item["link_type"] == "Report"
				and item.get("report", {}).get("report_type") in ("Query Report", "Script Report")
			}
		),
		"create": creatable,
		"closed_lookups": sorted(
			[doctype, field] for profile in profiles for doctype, field in REVIEWED_CLOSED.get(profile, ())
		),
	}


def sidebar_items(workspaces: list[str]) -> list[dict]:
	"""The items of the sidebars the user's desk shows, as Frappe filters them for the user:
	those of the desk icons the user sees, and those of the workspaces."""
	sidebars = get_sidebar_items(workspaces)
	icons = get_desktop_icons(bootinfo=frappe._dict(workspace_sidebar_item=sidebars))
	shown = {icon.label.lower() for icon in icons if not icon.hidden} | {name.lower() for name in workspaces}
	return [item for label in sorted(shown & set(sidebars)) for item in sidebars[label]["items"]]


def scope_record_types() -> set[str]:
	"""The regulated scope: the permission matrix and this app's own record types. Custom
	record types are left out: none ships with this app, and tests make their own."""
	own = frappe.get_all(
		"DocType",
		filters={"module": ["in", frappe.get_module_list("meddevice_suite")], "custom": 0},
		pluck="name",
	)
	return {*PERMISSIONS, *own}


def fillable_form(doctype: str) -> bool:
	"""A record type with a new form of its own that the session user may create and fill in.

	Left out: record types the desk offers no new form for ("User Cannot Create"), those the
	system writes (logs: every field is read-only, for everyone), and regulated record types
	the user's roles create only through transactions or a service (roles.fills_in_by_hand)."""
	meta = frappe.get_meta(doctype)
	if meta.istable or meta.issingle or meta.in_create or not frappe.has_permission(doctype, "create"):
		return False
	if all(df.read_only for df in meta.fields if df.fieldtype not in frappe.model.no_value_fields):
		return False
	if doctype not in PERMISSIONS:
		return True
	roles = frappe.get_roles()
	return any(
		fills_in_by_hand(doctype, role, flags)
		for role, flags in PERMISSIONS[doctype].items()
		if role in roles
	)
