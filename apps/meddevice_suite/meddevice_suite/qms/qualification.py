"""Equipment qualification, IQ, OQ and PQ (module 40; URS-EQP-010, URS-EQP-011, URS-EQP-012; ISO 13485
7.5.6, 6.3).

Each stage is an Equipment Qualification against an effective protocol (an SOP or a form), with
its tests, each passed or failed, recorded once it is done, never dated ahead (as calibration and
maintenance are: a future date would qualify the equipment from a day that has not come); it passes only when every test passes. Its performer executes it
with a signature; QA, never the performer, approves it with a signature, which completes it, or
returns it with a signed reason; once approved, it is never deleted and is corrected only by QA's
signed Correct (plan D-29, `equipment_care.correct`), which cancels it and opens its amendment, to
be executed and approved again. An OQ needs an approved, passed IQ before it; a PQ an approved,
passed OQ done on or after that IQ. Equipment that needs qualification is qualified while its
latest approved IQ, OQ and PQ each passed, in that order, and the PQ is within the requalification
interval QA set on the equipment, if one is set; after a repair, a PQ done on or after it; after a
move (an asset movement or a change of its workstation), an IQ (and so the OQ and PQ after it) done
on or after it (URS-EQP-013).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, now_datetime

from meddevice_suite.compliance.checks import QA_ROLE, require
from meddevice_suite.compliance.esign import sign
from meddevice_suite.master_data import equipment
from meddevice_suite.qms import equipment_care

DOCTYPE = "Equipment Qualification"
STAGES = ("IQ", "OQ", "PQ")
PROTOCOL_TYPES = ("SOP", "Form")
PERFORMERS = frozenset({"Maintenance User", "Manufacturing User", "Quality Manager"})
REQUALIFICATION_DAYS = equipment.REQUALIFICATION_DAYS
# What the performer signs for: changing any of it after execution needs execution again.
EXECUTED_CONTENT = ("asset", "stage", "occasion", "protocol", "performed_on")
# Set only by the signed acts. A draft saved through the form or the API keeps what is stored, and a
# new one, Frappe's Amend among them (it copies them from the cancelled record), holds none.
SET_BY_ACTS = ("executed_by", "executed_at", "approved_by", "approved_at")


def only_by_approval(doc: Document, method: str | None = None) -> None:
	if doc.get("_action") == "submit" and not doc.flags.qualification_approved:
		frappe.throw(_("A qualification is completed by QA's signed approval."), frappe.PermissionError)


def latest_passed(asset: str, stage: str, before_name: str | None = None) -> frappe._dict | None:
	filters = {"asset": asset, "stage": stage, "docstatus": 1, "outcome": "Pass"}
	if before_name:
		filters["name"] = ["!=", before_name]
	rows = frappe.get_all(
		DOCTYPE, filters=filters, fields=["name", "performed_on"], order_by="performed_on desc", limit=1
	)
	return rows[0] if rows else None


def evaluate(doc: Document, method: str | None = None) -> None:
	if doc.docstatus != 0:
		return
	asset = frappe.db.get_value(
		"Asset", doc.asset, [equipment.APPROVAL_STATUS, equipment.QUALIFICATION], as_dict=True
	)
	require(asset, _("Equipment {0} does not exist.").format(doc.asset))
	require(
		asset.get(equipment.APPROVAL_STATUS) == equipment.APPROVED,
		_("{0} is not approved equipment.").format(doc.asset),
	)
	require(cint(asset.get(equipment.QUALIFICATION)), _("{0} does not need qualification.").format(doc.asset))
	equipment_care.require_done(doc.performed_on, _("A qualification is recorded once it is done."))
	protocol = frappe.db.get_value(
		"Controlled Document", doc.protocol, ["status", "document_type"], as_dict=True
	)
	require(
		protocol and protocol.status == "Effective" and protocol.document_type in PROTOCOL_TYPES,
		_("The protocol is an effective SOP or form."),
	)
	if doc.stage in ("OQ", "PQ"):
		iq = latest_passed(doc.asset, "IQ", doc.name)
		require(iq, _("An OQ or PQ follows an approved, passed IQ."))
	if doc.stage == "PQ":
		oq = latest_passed(doc.asset, "OQ", doc.name)
		require(
			oq and getdate(oq.performed_on) >= getdate(iq.performed_on),
			_("A PQ follows an approved, passed OQ done on or after the IQ."),
		)
	require(doc.tests and all(row.outcome for row in doc.tests), _("Each test is passed or failed."))
	doc.outcome = "Pass" if all(row.outcome == "Pass" for row in doc.tests) else "Fail"
	if doc.flags.qualification_signed:
		return
	before = None if doc.is_new() else doc.get_doc_before_save()
	for field in SET_BY_ACTS:
		doc.set(field, before.get(field) if before else None)
	if before and before.executed_by and changed_since(before, doc):
		doc.executed_by, doc.executed_at = None, None  # what was executed changed: execute it again


def tests_of(doc: Document) -> list[tuple]:
	return [(row.test, row.acceptance_criteria, row.observed, row.outcome) for row in doc.tests]


def changed_since(before: Document, doc: Document) -> bool:
	return tests_of(before) != tests_of(doc) or any(
		str(before.get(field) or "") != str(doc.get(field) or "") for field in EXECUTED_CONTENT
	)


def execute(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: the performer executes the protocol and records its tests."""
	require(doc.docstatus == 0 and not doc.executed_by, _("{0} is executed already.").format(doc.name))
	require(
		PERFORMERS & set(frappe.get_roles()),
		_("Maintenance, production or QA executes a qualification."),
		frappe.PermissionError,
	)
	evaluate(doc)
	doc.executed_by, doc.executed_at = frappe.session.user, now_datetime()
	sign(doc, "Verified", "Execute", password, reason)
	doc.flags.qualification_signed = True
	doc.save()


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA, not its performer, approves the executed qualification."""
	require(doc.docstatus == 0 and doc.executed_by, _("{0} is approved once executed.").format(doc.name))
	require(QA_ROLE in frappe.get_roles(), _("Only QA approves a qualification."), frappe.PermissionError)
	require(
		frappe.session.user != doc.executed_by,
		_("Its performer does not approve it."),
		frappe.PermissionError,
	)
	doc.approved_by, doc.approved_at = frappe.session.user, now_datetime()
	sign(doc, "Approved", "Approve", password, reason)
	doc.flags.qualification_signed = doc.flags.qualification_approved = True
	doc.flags.ignore_permissions = True  # the signed approval is the only submit
	doc.submit()


def return_for_rework(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA returns an executed qualification to be done again, saying why."""
	require(doc.docstatus == 0 and doc.executed_by, _("{0} is returned once executed.").format(doc.name))
	require(QA_ROLE in frappe.get_roles(), _("Only QA returns a qualification."), frappe.PermissionError)
	require(reason, _("Say what must be done again."))
	sign(doc, "Rejected", "Return", password, reason)
	doc.executed_by, doc.executed_at = None, None
	doc.flags.qualification_signed = True
	doc.save()


def not_qualified(
	assets: list[str], on, repaired: dict | None = None, moved: dict | None = None
) -> dict[str, str]:
	"""Why each asset needing qualification is not qualified on the day; qualified ones left out.
	`repaired` and `moved` give the day of an asset's latest completed repair and relocation (an
	asset movement or a change of workstation, `equipment_care.repaired_and_moved`). A qualification
	on the day of the move counts as after it: both are known by their day."""
	if not assets:
		return {}
	day = getdate(on)
	rows = frappe.get_all(
		DOCTYPE,
		filters={"asset": ["in", assets], "docstatus": 1},
		fields=["asset", "stage", "outcome", "performed_on"],
		order_by="performed_on desc, creation desc",
	)
	latest: dict[tuple[str, str], frappe._dict] = {}
	for row in rows:
		latest.setdefault((row.asset, row.stage), row)
	interval = dict(
		frappe.get_all(
			"Asset", filters={"name": ["in", assets]}, fields=["name", REQUALIFICATION_DAYS], as_list=True
		)
	)
	repaired, moved = repaired or {}, moved or {}
	why = {}
	for asset in assets:
		stages = [latest.get((asset, stage)) for stage in STAGES]
		if not all(stages):
			why[asset] = _("it is not qualified (IQ, OQ and PQ)")
		elif any(stage.outcome != "Pass" for stage in stages):
			why[asset] = _("its latest qualification did not pass")
		elif not (
			getdate(stages[0].performed_on)
			<= getdate(stages[1].performed_on)
			<= getdate(stages[2].performed_on)
		):
			why[asset] = _("its qualification is out of order (IQ, OQ, PQ)")
		elif (
			cint(interval.get(asset))
			and getdate(add_days(stages[2].performed_on, cint(interval[asset]))) < day
		):
			why[asset] = _("its requalification was due on {0}").format(
				frappe.format_value(
					add_days(stages[2].performed_on, cint(interval[asset])), {"fieldtype": "Date"}
				)
			)
		elif asset in moved and getdate(stages[0].performed_on) < moved[asset]:
			why[asset] = _("it was moved on {0} and has not been qualified again since (IQ, OQ, PQ)").format(
				frappe.format_value(moved[asset], {"fieldtype": "Date"})
			)
		elif asset in repaired and getdate(stages[2].performed_on) < repaired[asset]:
			why[asset] = _("it was repaired on {0} and has had no PQ since").format(
				frappe.format_value(repaired[asset], {"fieldtype": "Date"})
			)
	return why
