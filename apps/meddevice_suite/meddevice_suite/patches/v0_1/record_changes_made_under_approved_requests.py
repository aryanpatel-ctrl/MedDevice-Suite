"""Record, on each change request still approved for implementation, the change already made under
it to each record it lists (URS-QMS-033; found in the foundation review of 2026-10-12).

Before this release a change made under a request was not recorded against its listing, so the
request authorised the same record's change again. Only a request still Approved authorises
anything, so only its listings are brought up to date. The record each change was made in is found
where the change left it: a document's revision and a risk file's assessment name the request; a
master, a specification and Quality Settings keep the request they were last changed under. A
master changed again since under another request no longer shows the earlier one; that listing is
left unrecorded and logged for QA to look at. Each recording is an update after submission of the
request with the reason below, in the audit trail; running the patch again changes nothing.
"""

import frappe

from meddevice_suite.master_data.versioning import CHANGED_UNDER, MASTERS
from meddevice_suite.qms.change_control import CHANGE_RECORD, DOCTYPE, record_change

REASON = "Migration: the change already made under this request, recorded against its listing"
QUALITY_SETTINGS = "Quality Settings"


def execute() -> None:
	approved = frappe.get_all(DOCTYPE, filters={"status": "Approved", "docstatus": 1}, pluck="name")
	if not approved:
		return
	unrecorded = []
	# One-off: the few requests in implementation, each listing a few records.
	for row in frappe.get_all(
		"Change Request Affected Record",
		filters={"parenttype": DOCTYPE, "parent": ["in", approved], CHANGE_RECORD: ["is", "not set"]},
		fields=["parent", "reference_doctype", "reference_name"],
		order_by="parent asc, idx asc",
	):
		made_in = change_made(row.parent, row.reference_doctype, row.reference_name)
		if made_in:
			record_change(row.parent, row.reference_doctype, row.reference_name, *made_in, reason=REASON)
		elif row.reference_doctype in MASTERS:
			unrecorded.append(f"{row.parent}: {row.reference_doctype} {row.reference_name}")
	if unrecorded:
		# Not an error: either nothing was changed yet, or a later change under another request hides it.
		frappe.log_error(
			title="Change requests: listings with no change found under them",
			message="\n".join(unrecorded),
		)


def change_made(change_request: str, doctype: str, name: str) -> tuple[str, str] | None:
	"""The record the change of `doctype` `name` under the request was made in, if one is found."""
	if doctype == "Controlled Document":
		revision = frappe.db.get_value(
			"Document Revision",
			{"controlled_document": name, "change_request": change_request},
			"name",
			order_by="creation asc",
		)
		return ("Document Revision", revision) if revision else None
	if doctype == "Risk Management File":
		assessment = frappe.db.get_value(
			"Risk Assessment",
			{"risk_management_file": name, "change_request": change_request, "status": ["!=", "Withdrawn"]},
			"name",
			order_by="creation asc",
		)
		return ("Risk Assessment", assessment) if assessment else None
	if doctype == QUALITY_SETTINGS:
		changed_under = frappe.db.get_single_value(QUALITY_SETTINGS, "changed_under")
		return (QUALITY_SETTINGS, QUALITY_SETTINGS) if changed_under == change_request else None
	if doctype in MASTERS and frappe.get_meta(doctype).has_field(CHANGED_UNDER):
		changed_under = frappe.db.get_value(doctype, name, CHANGED_UNDER)
		return (doctype, name) if changed_under == change_request else None
	return None
