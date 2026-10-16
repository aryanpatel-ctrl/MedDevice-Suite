"""Important files repository (module 61; URS-PLT-018, URS-PLT-019; DFD 9E: E1 create category,
E2 create sub-group, E3 upload document, E4 version control, E5 access control, E6 search and
retrieve; data store D9 Documents).

Files that are not controlled SOPs (licences, regulatory submissions, supplier and sterilization
certificates, audit reports) are kept here. QA keeps a tree of categories and sub-groups and says,
for each, which jobs read its files and which upload them; a sub-group that names no jobs takes
its group's. A file is a record of its versions: each upload is the next version, a private file
fingerprinted as it is added, with who added it, when, what changed and, for a licence or a
certificate, until when it is valid. Nothing is replaced or deleted: an earlier version stays,
its file is evidence that cannot be changed or removed, and a file no longer needed is archived
with a reason. People see only the files of the categories their jobs may read; QA and the
system administrator see all. A file's validity is tracked by the reminders (module 60) until a
new version renews it or it is archived.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from meddevice_suite.compliance.checks import QA_ROLE, require
from meddevice_suite.qms.document_control import file_fingerprint

CATEGORY = "File Category"
DOCTYPE = "Important File"
ACTIVE, ARCHIVED = "Active", "Archived"
SEES_ALL = frozenset({QA_ROLE, "System Manager"})
READERS, EDITORS = "readers", "editors"
NEW = ("new_file", "new_valid_until", "new_note")
SET_BY_RULES = ("status", "archived_reason", "current_version", "current_file", "valid_until")


# --- Who reads and uploads a category's files ---------------------------------------------------


def access() -> dict[str, dict[str, set[str]]]:
	"""Every category's reading and uploading jobs, a sub-group naming none taking its nearest
	group's: the whole tree in two reads."""
	categories = frappe.get_all(CATEGORY, fields=["name", "lft", "rgt"], order_by="lft asc")
	rows = frappe.get_all(
		"File Category Role", filters={"parenttype": CATEGORY}, fields=["parent", "parentfield", "role"]
	)
	own: dict[tuple[str, str], set[str]] = {}
	for row in rows:
		own.setdefault((row.parent, row.parentfield), set()).add(row.role)
	resolved: dict[str, dict[str, set[str]]] = {}
	for category in categories:  # groups come before what they hold (ordered by lft)
		groups = [c for c in categories if c.lft < category.lft and c.rgt > category.rgt]
		resolved[category.name] = {}
		for kind in (READERS, EDITORS):
			nearest = next(
				(own[(c.name, kind)] for c in [category, *reversed(groups)] if own.get((c.name, kind))), set()
			)
			resolved[category.name][kind] = nearest
	return resolved


def may(category: str, kind: str, user: str | None = None, known: dict | None = None) -> bool:
	"""Whether the person's jobs read (or upload) the category's files. QA keeps the repository
	and does both; the system administrator may read; whoever uploads a category's files reads them."""
	roles = set(frappe.get_roles(user or frappe.session.user))
	if QA_ROLE in roles or (kind == READERS and "System Manager" in roles):
		return True
	jobs = (known or access()).get(category, {})
	allowed = set(jobs.get(EDITORS, set()))
	if kind == READERS:
		allowed |= jobs.get(READERS, set())
	return bool(roles & allowed)


def readable_categories(user: str) -> list[str]:
	known = access()
	return [name for name in known if may(name, READERS, user, known)]


# --- Categories ---------------------------------------------------------------------------------


def check_category(doc: Document) -> None:
	"""File Category validate: jobs are business jobs; a category holding files is not a group."""
	from meddevice_suite.setup.roles import BUSINESS_ROLES  # the jobs that work with the files

	for row in (*doc.get(READERS), *doc.get(EDITORS)):
		require(row.role in BUSINESS_ROLES, _("{0} is not one of the business jobs.").format(row.role))
	if doc.is_group and not doc.is_new():
		require(
			not frappe.db.exists(DOCTYPE, {"category": doc.name}),
			_("{0} holds files; a group holds sub-groups only.").format(doc.name),
		)


def protect_category(doc: Document) -> None:
	require(
		not frappe.db.exists(DOCTYPE, {"category": doc.name}),
		_("{0} holds files and is not deleted.").format(doc.name),
		frappe.PermissionError,
	)


# --- Files --------------------------------------------------------------------------------------


def check_file(doc: Document) -> None:
	"""Important File validate: uploaded by its category's jobs, each upload a new version, nothing
	earlier changed, an archived file left as it is."""
	require(
		not frappe.db.get_value(CATEGORY, doc.category, "is_group"),
		_("Files are kept in a category, not in the group {0}.").format(doc.category),
	)
	if doc.flags.important_file:
		return
	user = frappe.session.user
	require(
		may(doc.category, EDITORS),
		_("Your jobs do not upload files of {0}.").format(doc.category),
		frappe.PermissionError,
	)
	if doc.is_new():
		for field in SET_BY_RULES:
			doc.set(field, None)
		doc.status = ACTIVE
		doc.set("versions", [])
		require(doc.new_file, _("Attach the file."))
	else:
		before = doc.get_doc_before_save()
		require(before.status != ARCHIVED, _("{0} is archived.").format(doc.name), frappe.PermissionError)
		# Compared by value, not by type: the desk sends the record's dates and times back as text,
		# while the record as stored holds them as dates (a renewal from the form was refused).
		require(
			not any(doc.has_value_changed(field) for field in SET_BY_RULES),
			_("The status, the current version and its validity follow from the versions."),
			frappe.PermissionError,
		)
		require(
			[row.as_dict(no_default_fields=True, convert_dates_to_str=True) for row in before.versions]
			== [row.as_dict(no_default_fields=True, convert_dates_to_str=True) for row in doc.versions],
			_("Earlier versions are kept as they were."),
			frappe.PermissionError,
		)
		if before.category != doc.category:
			require(
				may(before.category, EDITORS),
				_("Your jobs do not upload files of {0}.").format(before.category),
				frappe.PermissionError,
			)
	if doc.new_file:
		add_version(doc, user)


def add_version(doc: Document, user: str) -> None:
	version = (doc.current_version or 0) + 1
	require(version == 1 or (doc.new_note or "").strip(), _("Say what changed in this version."))
	doc.append(
		"versions",
		{
			"version": version,
			"file": doc.new_file,
			"file_sha256": file_fingerprint(doc.new_file),  # private and present, or refused
			"valid_until": doc.new_valid_until,
			"note": (doc.new_note or "").strip() or (_("First version") if version == 1 else None),
			"uploaded_by": user,
			"uploaded_on": now_datetime(),
		},
	)
	doc.current_version, doc.current_file, doc.valid_until = version, doc.new_file, doc.new_valid_until
	for field in NEW:
		doc.set(field, None)


@frappe.whitelist()
def archive(name: str, reason: str) -> None:
	"""The category's uploaders, or QA, archive a file no longer needed, saying why."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	require(
		may(doc.category, EDITORS),
		_("Your jobs do not upload files of {0}.").format(doc.category),
		frappe.PermissionError,
	)
	require(doc.status == ACTIVE, _("{0} is archived already.").format(name))
	reason = (reason or "").strip()
	require(reason, _("Say why it is archived."))
	doc.status, doc.archived_reason = ARCHIVED, reason
	doc.flags.important_file = True
	doc.flags.audit_reason = reason
	doc.save(ignore_permissions=True)  # checked above, beyond the record type's permissions


def protect_file(doc: Document) -> None:
	frappe.throw(_("A file in the repository is never deleted; archive it instead."), frappe.PermissionError)


# --- Who sees which files ---------------------------------------------------------------------------


def query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if set(frappe.get_roles(user)) & SEES_ALL:
		return ""
	categories = readable_categories(user)
	if not categories:
		return "1 = 0"
	return "`tabImportant File`.`category` in ({})".format(", ".join(frappe.db.escape(c) for c in categories))


def has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	if doc.is_new():
		return True  # a new file is checked against its category when it is saved
	looks = ptype in (None, "read", "report", "print", "select")
	return may(doc.category, READERS if looks else EDITORS, user)
