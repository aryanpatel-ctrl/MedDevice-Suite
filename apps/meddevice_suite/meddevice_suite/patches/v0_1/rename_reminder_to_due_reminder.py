"""Give Frappe back its own Reminder: the reminders of module 60 become "Due Reminder" (rules section 4).

They were the record type "Reminder", the name of Frappe's own record type (module Automation):
one name, so one table and one definition for both. Every migrate synced the two definitions over
each other, which turned what each reminder's people were told into "0" (Frappe's default); Frappe's
log clearing deletes its Reminder rows after 30 days; and Frappe's own reminder jobs read the same
table (found on 2026-10-16).

Run before the definitions are synced, on a site where "Reminder" still holds this app's reminders:

- its table becomes `tabDue Reminder`, every reminder in it, and its definition is renamed with it,
  as Frappe renames a record type (frappe/model/rename_doc.py);
- what names a reminder by its record type and name follows it: comments, attachments, versions,
  notices, assignments, shares and other records' links to it. A regulated record changed so gets an
  audit event saying why, as any change to it does;
- the audit events and electronic signatures recorded under "Reminder" are not changed. They are
  evidence, and the name is part of each one's fingerprint: rewritten, the chains would read as
  tampered with. The audit trail reads them as the reminders' history under their former name
  (compliance/audit_scope.FORMER_NAMES);
- what names the record type alone is not changed here. The Overdue alert and the definition come
  from their files in the sync that follows, the sidebar and permissions from apply_configuration;
  the rest is Frappe's own (Log Settings clears Frappe's Reminder, and keeps doing so).

The same migrate then syncs Frappe's Reminder, missing now, and creates its table afresh. Frappe's
columns left in `tabDue Reminder` (user, remind_at, reminder_doctype, reminder_docname,
description) are kept: nothing reads them, and dropping columns of a regulated table could only
lose data. Nothing is done where "Reminder" is Frappe's: a new site, or once this has run.
"""

import frappe
from frappe import _
from frappe.model.dynamic_links import get_dynamic_links
from frappe.model.rename_doc import get_link_fields, rename_parent_and_child
from frappe.model.utils.user_settings import sync_user_settings
from frappe.query_builder import Field

from meddevice_suite.compliance import audit
from meddevice_suite.compliance.audit_scope import SELF_RECORDING, is_regulated
from meddevice_suite.work_management.reminders import DOCTYPE

OLD = "Reminder"
MODULE = "Work Management"
REASON = (
	'The record type "Reminder" of module 60 was renamed "Due Reminder": Frappe has a record type of '
	"that name (patch rename_reminder_to_due_reminder)."
)

# Record types that keep a record's name as plain text beside the field naming its record type,
# where most use a Dynamic Link (found from the definitions of Frappe 16).
NAME_BESIDE: dict[tuple[str, str], str] = {
	("File", "attached_to_doctype"): "attached_to_name",
	("Version", "ref_doctype"): "docname",
	("Error Log", "reference_doctype"): "reference_name",
	("Notification Log", "document_type"): "document_name",
	("Email Queue", "reference_doctype"): "reference_name",
	("Milestone", "reference_type"): "reference_name",
}


def execute() -> None:
	module = frappe.db.get_value("DocType", OLD, "module")
	if frappe.db.exists("DocType", DOCTYPE):
		if module == MODULE:
			frappe.throw(
				_("Both {0} and {1} are defined on this site: reconcile them by hand, then migrate.").format(
					OLD, DOCTYPE
				)
			)
		return  # renamed already, or a site this app was installed on after the rename
	if module != MODULE and not holds_the_reminders():
		return
	# The table first: renaming it commits, so it is done before anything else is written, and a
	# run that fails after it finds the table moved and goes on from there.
	move_table(OLD, DOCTYPE)
	rename_parent_and_child("DocType", OLD, DOCTYPE, frappe.get_meta("DocType"))
	frappe.clear_cache(doctype=OLD)
	move_record_references(OLD, DOCTYPE)
	move_list_settings(OLD, DOCTYPE)
	frappe.clear_cache()


def holds_the_reminders() -> bool:
	"""Whether the reminders are still under Frappe's definition of "Reminder": a migrate that
	stopped after syncing Frappe's definition over this app's, and before syncing this app's again,
	leaves it on the shared table, which still has the reminders' columns; or one that stopped after
	this patch renamed the table."""
	if frappe.db.table_exists(DOCTYPE, cached=False):
		return True
	return frappe.db.table_exists(OLD, cached=False) and "reminder_key" in frappe.db.get_table_columns(OLD)


def move_table(old: str, new: str) -> None:
	old_exists, new_exists = (
		frappe.db.table_exists(old, cached=False),
		frappe.db.table_exists(new, cached=False),
	)
	if old_exists and new_exists:
		frappe.throw(
			_(
				"Both tables tab{0} and tab{1} exist on this site: reconcile them by hand, then migrate."
			).format(old, new)
		)
	if old_exists:
		frappe.db.rename_table(old, new)
	# Frappe forgets its list of tables after CREATE and DROP only; the sync that follows reads it
	# to decide whether to create a table or alter it.
	frappe.client_cache.delete_value("db_tables")
	for table in (old, new):
		frappe.client_cache.delete_value(f"table_columns::tab{table}")


def move_record_references(old: str, new: str) -> None:
	"""Every field naming the record type `old` beside the name of one of the records now in `new`
	is set to `new`, but in the audit trail and the signatures, which keep the name they were
	recorded under. References to records of a Frappe Reminder are left alone: their names are
	not in the table of `new`."""
	frappe.flags.link_fields = {}  # get_link_fields remembers its answer for the process
	names_beside = {(field.parent, field.options): field.fieldname for field in get_dynamic_links()}
	names_beside.update(NAME_BESIDE)
	for field in get_link_fields("DocType"):
		name_field = names_beside.get((field.parent, field.fieldname))
		if field.issingle or not name_field or field.parent in SELF_RECORDING or field.parent == new:
			continue
		if not frappe.db.table_exists(field.parent):
			continue
		columns = frappe.db.get_table_columns(field.parent)
		if field.fieldname in columns and name_field in columns:
			move_in(field.parent, field.fieldname, name_field, old, new)


def move_in(holder: str, field: str, name_field: str, old: str, new: str) -> None:
	table, records = frappe.qb.DocType(holder), frappe.qb.DocType(new)
	matching = (table[field] == old) & table[name_field].isin(frappe.qb.from_(records).select(records.name))
	meta = frappe.get_meta(holder)
	changed = []
	if meta.istable:
		changed = [
			row
			for row in frappe.qb.from_(table)
			.select(table.name, table.parent, table.parenttype, table.parentfield)
			.where(matching)
			.run(as_dict=True)
			if row.parenttype and is_regulated(row.parenttype, frappe.get_meta(row.parenttype).module)
		]
	elif is_regulated(holder, meta.module):
		changed = frappe.qb.from_(table).select(table.name).where(matching).run(as_dict=True)
	frappe.qb.update(table).set(table[field], new).where(matching).run()
	# Written as the rename's own change, so each regulated record's trail says what changed and why.
	for row in changed:
		if meta.istable:
			change = {"row_changed": [[row.parentfield, row.name, [[field, old, new]]]]}
			audit.record(row.parenttype, row.parent, "Updated", change, REASON)
		else:
			audit.record(holder, row.name, "Updated", {"changed": [[field, old, new]]}, REASON)


def move_list_settings(old: str, new: str) -> None:
	"""The columns, filters and sorting people saved for the reminders' list and report views."""
	sync_user_settings()  # those still only in the cache first, as Frappe's rename does
	settings = frappe.qb.Table("__UserSettings")
	frappe.qb.update(settings).set(Field("doctype"), new).where(Field("doctype") == old).run()
	frappe.cache.delete_key("_user_settings")
