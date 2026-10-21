"""What the browser test sheet of D12 Audit Logs starts from (module 9F; URS-CMP-001 to URS-CMP-042,
URS-PLT-012, URS-PLT-013), on a test site only (cypress/integration/sheet_d12.js).

The sheet's tests act on the screens as Purchase, Management, Sales, Stores, QA and the System
Administrator: the audit trail records what they do, QA reviews and verifies it, signs, anchors the
trail, archives a period and changes Quality Settings under change control; the other jobs and the
System Administrator are refused what is not theirs. Each test is given records of its own, made
afresh each run, each step by the job that does it (`acting_as`) through the code's own services
and signed acts:

- purchase orders of the journey's material from the journey's supplier, raised by Purchase, in
  draft or approved by Management;
- a measuring instrument recorded by QA, ready for QA's approval, or approved and calibrated by
  Maintenance with a signature (the equipment of D10, made by its own set-up's functions);
- a change request awaiting QA's approval, assessed by QA with a signature; approved change requests
  that list Quality Settings, and Quality Settings brought back to the values the sheet starts from;
- a lot QA released with a signature;
- a record archive QA requested of a day long past, which the background job builds;
- a workstation of the sheet's own with several audit events, whose trail D12-T22 tampers with.

Tampering (D12-T22) is done as an attacker with database access would do it: the record's audit
events are deleted straight from the database. The test site is left as it was found afterwards
(`restore_audit_trail`): the deleted rows are put back exactly as they were and the anchors made
since the tampering began are removed from the database and the anchor file, as restoring the
database and the server's files from the backup taken before would. Without that, every later
check of the whole trail on the site would report the removal: an anchor carries the count of
entries forward on purpose (compliance/audit_anchor.py).

`refusals_past_permissions` tries, as the Administrator skipping every permission, to insert, change
and delete an evidence record, inside a savepoint that is always rolled back, and returns what each
attempt was refused with: the record itself refuses, whatever permissions its caller holds or skips.

Only the Administrator who runs the tests prepares the sheet.
"""

import json
import os
import zipfile
from contextlib import AbstractContextManager
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, get_fullname, today

from meddevice_suite.compliance import audit_anchor, quality_settings
from meddevice_suite.compliance.archive import archive_file_path
from meddevice_suite.compliance.audit_scope import SELF_RECORDING
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.lot_control import stores
from meddevice_suite.qa.sheets import d10
from meddevice_suite.qa.sheets.d4 import MATERIAL, ensure_supplier_approved, lot_up_to
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	CUSTOMER,
	PRODUCT,
	RAW_MATERIALS,
	SUPPLIER,
	acting_as,
)
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.qms import change_control
from meddevice_suite.setup.roles import JOB_PROFILES

QA, PURCHASE, MANAGEMENT, MAINTENANCE = "QA Manager", "Purchase", "Management", "Maintenance"
PRODUCTION = "Production Supervisor"
REASON = "Browser test sheet D12"
SETTINGS = quality_settings.DOCTYPE
# The values of Quality Settings the sheet's tests start from (D12-T16, D12-T17): the decided ones.
SHEET_SETTINGS = ("login_lockout_attempts", "lot_expiry_warning_days")
PURCHASE_STAGES = ("draft", "approved")
# The record type and name prefix of the sheet's own workstations, the only records whose trail
# the sheet tampers with.
TRAIL_DOCTYPE = "Workstation"
TRAIL_PREFIX = "UIJ D12 Trail"
# Where the audit events removed by the tampering are kept until they are put back.
REMOVED_KEY = "meddevice_suite:qa:d12:removed_audit_events"
# The evidence records `refusals_past_permissions` tries, besides those that record themselves.
ARCHIVE = "Record Archive"
EVIDENCE_TYPES = (*sorted(SELF_RECORDING), ARCHIVE)
# A day long past, in days before today: an archive of it holds next to nothing and is built at once.
ARCHIVED_DAY = -3650


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


def act_as(profile: str) -> AbstractContextManager:
	return acting_as(email_for(profile))


def require_password(password: str | None) -> str:
	if not password:
		frappe.throw(_("Give the run's password of the job users."))
	return password


def tag() -> str:
	return frappe.generate_hash(length=10).upper()


# --- The sheet's masters ------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""QA's approval of the journey's supplier for its raw materials, and the external calibration
	laboratory of the equipment, after the journey's masters (ui_journey.prepare_journey); and what
	the tests work with, with each job's full name as the audit trail and the signatures show it."""
	require_administrator()
	require_password(password)
	ensure_supplier_approved(password, [*RAW_MATERIALS])
	d10.ensure_laboratory()
	return {
		"company": COMPANY,
		"supplier": SUPPLIER,
		"material": MATERIAL,
		"customer": CUSTOMER,
		"product": PRODUCT,
		"quarantine": stores.store_for(stores.QUARANTINE, COMPANY),
		"names": {profile: get_fullname(email_for(profile)) for profile in JOB_PROFILES},
	}


# --- Records of the tests, through the jobs -----------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_purchase_order(password: str, stage: str = "draft") -> dict:
	"""A purchase order of 10 of the journey's material from the journey's supplier, raised by
	Purchase: in draft, or sent for approval and approved by Management (submitted)."""
	require_administrator()
	require_password(password)
	if stage not in PURCHASE_STAGES:
		frappe.throw(_("Unknown stage {0}.").format(stage))
	from frappe.model.workflow import apply_workflow

	quarantine = stores.store_for(stores.QUARANTINE, COMPANY)
	with act_as(PURCHASE):
		order = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": SUPPLIER,
				"company": COMPANY,
				"schedule_date": add_days(today(), 7),
				"set_warehouse": quarantine,
				"items": [
					{
						"item_code": MATERIAL,
						"qty": 10,
						"schedule_date": add_days(today(), 7),
						"warehouse": quarantine,
					}
				],
			}
		).insert()
		if stage == "approved":
			apply_workflow(order, "Send for Approval")
	if stage == "approved":
		with act_as(MANAGEMENT):
			apply_workflow(frappe.get_doc("Purchase Order", order.name), "Approve")
	return {"order": order.name}


@frappe.whitelist(methods=["POST"])
def prepare_instrument(password: str, calibrated: int = 0) -> dict:
	"""A measuring instrument recorded by QA with all its approval needs, in draft for QA's signed
	approval; with `calibrated`, approved by QA and calibrated by an external laboratory, completed
	by Maintenance with a signature (qa/sheets/d10.py). Returns {asset, calibration?}."""
	require_administrator()
	require_password(password)
	asset = d10.new_equipment({})
	if not cint(calibrated):
		return {"asset": asset}
	d10.approved(asset, password)
	return {"asset": asset, "calibration": d10.calibrated(asset, password)}


def settings_change(password: str, approve: bool = True) -> str:
	"""A minor change request of the system listing Quality Settings, raised by the Production
	Supervisor and assessed by QA with a signature; with `approve`, approved by QA with a signature
	(module 36)."""
	with act_as(PRODUCTION):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Change Quality Settings {tag()} (browser test D12)",
				"change_type": change_control.CHANGE_TYPE_OF[SETTINGS],
				"description": "An alert lead time or the lockout adjusted (browser test D12).",
				"reason": "Found in the periodic review of the settings (browser test D12).",
				"affected_records": [{"reference_doctype": SETTINGS, "reference_name": SETTINGS}],
				"actions": [
					{
						"description": "Change Quality Settings",
						"action_owner": email_for(QA),
						"due_date": today(),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	with act_as(QA):
		request = frappe.get_doc("Change Request", request.name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_summary": "The settings only, within their bounds (browser test D12).",
				"classification": "Minor",
				"effectiveness_check_days": 90,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason=f"{REASON}: assessed")
		if approve:
			change_control.approve(
				frappe.get_doc("Change Request", request.name),
				password=password,
				reason=f"{REASON}: approved",
			)
	return request.name


@frappe.whitelist(methods=["POST"])
def prepare_change_awaiting_approval(password: str) -> dict:
	"""A change request awaiting QA's approval, assessed by QA with a signature, for QA to approve on
	the screen: its approval is chained to the assessment. Returns {change_request, assessment}."""
	require_administrator()
	require_password(password)
	name = settings_change(password, approve=False)
	assessment = frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": "Change Request", "reference_name": name, "action": "Assess"},
		pluck="name",
	)
	return {"change_request": name, "assessment": assessment[0]}


@frappe.whitelist(methods=["POST"])
def prepare_quality_settings(password: str) -> dict:
	"""Quality Settings at the values the sheet starts from (the decided ones of SHEET_SETTINGS),
	brought back by QA under an approved change request when an earlier run changed them, and an
	approved change request that lists Quality Settings for QA to name on the screen. Returns
	{change_request, version, values}."""
	require_administrator()
	require_password(password)
	defaults = {field: quality_settings.SETTINGS[field].default for field in SHEET_SETTINGS}
	if any(quality_settings.value(field) != default for field, default in defaults.items()):
		request = settings_change(password)
		with act_as(QA):
			settings = frappe.get_single(SETTINGS)
			settings.update({**defaults, quality_settings.CHANGE_REQUEST: request})
			settings.save()
	return {
		"change_request": settings_change(password),
		"version": cint(quality_settings.stored(quality_settings.VERSION) or 1),
		"values": {field: quality_settings.value(field) for field in SHEET_SETTINGS},
	}


@frappe.whitelist(methods=["POST"])
def prepare_released_lot(password: str) -> dict:
	"""A lot of the journey's material received, inspected and released by QA with a signature
	(qa/sheets/d4.py). Returns {lot, release}."""
	require_administrator()
	require_password(password)
	ensure_supplier_approved(password, [*RAW_MATERIALS])
	lot = lot_up_to("approved", password, MATERIAL, 10)
	return {"lot": lot["lot"], "release": lot["release"]}


@frappe.whitelist(methods=["POST"])
def prepare_archive() -> dict:
	"""A record archive QA requested of a day long past, which the background job builds; QA reads
	it on the screen once completed. Returns {archive}."""
	require_administrator()
	day = add_days(today(), ARCHIVED_DAY)
	with act_as(QA):
		archive = frappe.get_doc({"doctype": ARCHIVE, "from_date": day, "to_date": day}).insert()
	return {"archive": archive.name}


@frappe.whitelist(methods=["POST"])
def archive_contents(archive: str) -> dict:
	"""What the archive's ZIP file holds, read as anyone would read it without the system: the names
	of its files, and what a record's file and the manifest hold; and the File record of the archive's
	file as stored. Returns {files, record_file, record_keys, manifest_keys, file}."""
	require_administrator()
	doc = frappe.get_doc(ARCHIVE, archive)
	if doc.status != "Completed":
		frappe.throw(_("Archive {0} is not completed.").format(archive))
	stored_file = frappe.db.get_value(
		"File",
		{"file_url": doc.archive_file, "attached_to_doctype": ARCHIVE, "attached_to_name": archive},
		["name", "is_private", "file_url"],
		as_dict=True,
	)
	with zipfile.ZipFile(archive_file_path(doc)) as file:
		names = file.namelist()
		record = next((name for name in names if name.startswith("records/")), None)
		return {
			"files": names,
			"record_file": record,
			"record_keys": sorted(json.loads(file.read(record))) if record else [],
			"manifest_keys": sorted(json.loads(file.read("manifest.json"))),
			"file": stored_file,
		}


# --- The audit trail, tampered with as an attacker would (D12-T22) ------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_trail_record() -> dict:
	"""A workstation of the sheet's own, recorded by the Production Supervisor and changed three
	times: four audit events, the newest of the whole trail. Returns {doctype, name, events}, the
	events oldest first."""
	require_administrator()
	with act_as(PRODUCTION):
		workstation = frappe.get_doc(
			{
				"doctype": TRAIL_DOCTYPE,
				"workstation_name": f"{TRAIL_PREFIX} {tag()}",
				"production_capacity": 1,
			}
		).insert()
		for capacity in (2, 3, 4):
			workstation = frappe.get_doc(TRAIL_DOCTYPE, workstation.name)
			workstation.production_capacity = capacity
			workstation.save()
	return {
		"doctype": TRAIL_DOCTYPE,
		"name": workstation.name,
		"events": events_of(workstation.name),
	}


def events_of(name: str) -> list[str]:
	return frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": TRAIL_DOCTYPE, "reference_name": name},
		pluck="name",
		order_by="creation asc, name asc",
	)


@frappe.whitelist(methods=["POST"])
def remove_audit_events(name: str, newest: int = 0, oldest: int = 0) -> dict:
	"""Delete the sheet's workstation's `newest` and `oldest` audit events straight from the database,
	as an attacker with database access would, keeping them to be put back (`restore_audit_trail`).
	Returns {removed, events_left, anchor, head_removed}: the newest anchor of the audit trail, and
	whether its newest entry is among those removed."""
	require_administrator()
	if not str(name).startswith(TRAIL_PREFIX) or not frappe.db.exists(TRAIL_DOCTYPE, name):
		frappe.throw(_("{0} is not one of the sheet's workstations.").format(name))
	events = events_of(name)
	newest, oldest = cint(newest), cint(oldest)
	if newest < 0 or oldest < 0 or newest + oldest >= len(events):
		frappe.throw(_("{0} keeps at least one of its {1} audit events.").format(name, len(events)))
	removed = events[:oldest] + (events[len(events) - newest :] if newest else [])
	anchor = audit_anchor.newest_anchor("Audit Event")
	kept = frappe.cache.get_value(REMOVED_KEY) or {}
	if not kept:
		# The anchor before the tampering: anchors made after it carry the removal forward.
		kept = {"anchor": anchor.name if anchor else None, "rows": []}
	table = frappe.qb.DocType("Audit Event")
	kept["rows"] += (
		frappe.qb.from_(table).select(table.star).where(table.name.isin(removed)).run(as_dict=True)
	)
	frappe.cache.set_value(REMOVED_KEY, kept)
	frappe.db.delete("Audit Event", {"name": ["in", removed]})
	return {
		"removed": removed,
		"events_left": events_of(name),
		"anchor": {
			"name": anchor.name,
			"head_entry": anchor.head_entry,
			"entry_count": cint(anchor.entry_count),
		}
		if anchor
		else None,
		"head_removed": bool(anchor and anchor.head_entry in removed),
	}


@frappe.whitelist(methods=["POST"])
def restore_audit_trail() -> dict:
	"""Leave the test site's audit trail as it was before the tampering, as restoring the database and
	the server's files from the backup taken before would: the removed audit events put back exactly
	as they were, and the anchors of the audit trail made since removed from the database and from
	the anchor file. Nothing to do when nothing was removed. Returns {restored, anchors_removed}."""
	require_administrator()
	kept = frappe.cache.get_value(REMOVED_KEY)
	if not kept:
		return {"restored": [], "anchors_removed": []}
	rows = [row for row in kept["rows"] if not frappe.db.exists("Audit Event", row["name"])]
	if rows:
		columns = list(rows[0])
		table = frappe.qb.DocType("Audit Event")
		query = frappe.qb.into(table).columns(*columns)
		for row in rows:
			query = query.insert(*(row[column] for column in columns))
		query.run()
	since = frappe.db.get_value(audit_anchor.DOCTYPE, kept["anchor"], "creation") if kept["anchor"] else None
	filters = {"ledger": "Audit Event"}
	if since:
		filters["creation"] = [">", since]
	later = frappe.get_all(audit_anchor.DOCTYPE, filters=filters, pluck="name")
	if later:
		frappe.db.delete(audit_anchor.DOCTYPE, {"name": ["in", later]})
		without_anchor_lines(audit_anchor.anchor_file(), set(later))
	frappe.cache.delete_value(REMOVED_KEY)
	return {"restored": [row["name"] for row in rows], "anchors_removed": later}


def without_anchor_lines(path: Path, anchors: set[str]) -> None:
	"""The anchor file without the lines of `anchors`, replaced in one step."""
	if not path.exists():
		return
	lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
	kept = [line for line in lines if (audit_anchor.readable(line) or {}).get("anchor") not in anchors]
	if len(kept) == len(lines):
		return
	staged = path.with_name(f"{path.name}.restoring")
	staged.write_text("".join(kept), encoding="utf-8")
	os.replace(staged, path)


# --- What the evidence records refuse, whatever the caller's permissions --------------------------


def sample(doctype: str) -> dict:
	"""A record of the evidence type with its required values, as someone would try to insert it."""
	at = str(get_datetime())
	values = {
		"Audit Event": {
			"reference_doctype": "Item",
			"reference_name": MATERIAL,
			"document_name_at_event": MATERIAL,
			"action": "Updated",
			"user": email_for(QA),
			"event_at": at,
			"event_hash": "x",
		},
		"Electronic Signature": {
			"reference_doctype": "Item",
			"reference_name": MATERIAL,
			"document_name_at_signing": MATERIAL,
			"action": "Approve",
			"meaning": "Approved",
			"signer": email_for(QA),
			"signer_name": get_fullname(email_for(QA)),
			"signed_at": at,
			"record_hash": "x",
			"signature_hash": "x",
		},
		"Audit Anchor": {
			"ledger": "Audit Event",
			"head_entry": "x",
			"head_fingerprint": "x",
			"head_created": at,
			"entry_count": 1,
			"anchored_at": at,
			"trigger": "On request",
			"anchor_hash": "x",
		},
		"Audit Trail Finding": {
			"trail": "Audit Event",
			"kind": audit_anchor.ENTRIES_MISSING,
			"subject": "1",
			"detected_at": at,
			"finding": "x",
			"what_to_do": "x",
			"finding_key": "x",
		},
	}
	return {"doctype": doctype, **values[doctype]}


def refused(attempt) -> str:
	"""What the attempt was refused with, or "allowed"; whatever it did is rolled back."""
	frappe.db.savepoint("sheet_d12_attempt")
	try:
		attempt()
		outcome = "allowed"
	except (frappe.PermissionError, frappe.ValidationError) as error:
		outcome = str(error)
	finally:
		frappe.db.rollback(save_point="sheet_d12_attempt")
		frappe.clear_messages()  # the refusal is returned, not shown
	return outcome


def changed(doctype: str, name: str) -> None:
	doc = frappe.get_doc(doctype, name)
	if doctype == ARCHIVE:
		doc.from_date = add_days(doc.from_date, -1)
	else:
		field = {"Audit Anchor": "entry_count", "Audit Trail Finding": "finding"}.get(doctype, "reason")
		doc.set(field, 1 if field == "entry_count" else "x")
	doc.save(ignore_permissions=True)  # the attempt the record itself refuses


@frappe.whitelist(methods=["POST"])
def refusals_past_permissions(doctype: str, name: str | None = None) -> dict:
	"""As the Administrator, skipping every permission: insert a record of the evidence type, change
	`name` and delete it, each rolled back; what each attempt was refused with. Returns {insert?,
	change?, delete?}."""
	require_administrator()
	if doctype not in EVIDENCE_TYPES:
		frappe.throw(_("{0} is not one of the evidence records.").format(doctype))
	if name and not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} does not exist.").format(doctype, name))
	outcomes = {}
	if doctype in SELF_RECORDING:
		outcomes["insert"] = refused(
			lambda: frappe.get_doc(sample(doctype)).insert(ignore_permissions=True)  # the attempt refused
		)
	if name:
		outcomes["change"] = refused(lambda: changed(doctype, name))
		outcomes["delete"] = refused(lambda: frappe.delete_doc(doctype, name, ignore_permissions=True))
	return outcomes
