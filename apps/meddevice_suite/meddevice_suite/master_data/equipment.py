"""Equipment master (module 8; URS-MST-010; DFD D10 Equipment Master, 9D Equipment Management;
ISO 13485 6.3, 7.5.1, 7.6; 21 CFR 820.70(g), 820.72; 21 CFR 11.50, 11.70).

Each piece of equipment is an ERPNext Asset with what the plant needs to know of it: its
equipment tag, class, manufacturer, model and serial number, where it is used, whether it affects
product quality, and whether it needs calibration, preventive maintenance and qualification, with
their intervals. QA approves it with an electronic signature before it is used; until then, and
while it is out of service or retired, an inspection reading may not name it as its instrument.

- The tag is unique among equipment that is not cancelled (an amended asset keeps the tag of the
  one it replaces). What QA approved (the fields above, but where it stands, which ERPNext's
  asset movements change and record) changes once the equipment is approved only under an
  approved change request (URS-MST-001, master_data/versioning.py), on a submitted asset too.
  Its status (in service, out of service) is the plant's day-to-day record, in the audit trail;
  retired is final. ERPNext's own asset status (sold, scrapped, in maintenance, out of order,
  capitalized) also keeps it off inspections.
- The approval fields are set only by the signed act, on a draft and on a submitted asset
  alike (the rules run at validate and at update after submit).
- Approval is independent of ERPNext's asset submit, which is the accounting of the asset
  (accounting stays in Tally): an instrument bought before the system is approved as it is.
- Calibration and maintenance due dates, and refusing equipment past them, are module 38; the
  intervals here are what it schedules from. Qualification records are module 40.

The history card (module 62) is the report master_data/report/equipment_history_card.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from meddevice_suite.compliance.audit import same
from meddevice_suite.compliance.esign import sign

DOCTYPE = "Asset"
QA_ROLE = "Quality Manager"
APPROVAL_FLAG = "equipment_approval"
# Fields on the asset
TAG, CLASS, MAKER, MODEL, SERIAL, WORKSTATION, QUALITY_IMPACT = (
	"mds_equipment_tag",
	"mds_equipment_class",
	"mds_manufacturer",
	"mds_model",
	"mds_serial_number",
	"mds_workstation",
	"mds_affects_quality",
)
CALIBRATION, CALIBRATION_DAYS, MAINTENANCE, MAINTENANCE_DAYS, QUALIFICATION = (
	"mds_needs_calibration",
	"mds_calibration_interval_days",
	"mds_needs_maintenance",
	"mds_maintenance_interval_days",
	"mds_needs_qualification",
)
STATUS = "mds_equipment_status"
APPROVAL_STATUS, APPROVED_BY, APPROVED_AT = (
	"mds_equipment_approval",
	"mds_equipment_approved_by",
	"mds_equipment_approved_at",
)
APPROVAL_FIELDS = (APPROVAL_STATUS, APPROVED_BY, APPROVED_AT)
DRAFT, APPROVED = "Draft", "Approved"
IN_SERVICE, OUT_OF_SERVICE, RETIRED = "In service", "Out of service", "Retired"
STATUSES = (IN_SERVICE, OUT_OF_SERVICE, RETIRED)
CLASSES = (
	"Production machine",
	"Measuring instrument",
	"Test equipment",
	"Sterilization equipment",
	"Utility",
)
# What QA approves: the equipment's identity, use and care. Changed under change control once
# approved. Not the location: ERPNext's asset movements change it and are its record.
CONTROLLED_FIELDS = (
	TAG,
	CLASS,
	MAKER,
	MODEL,
	SERIAL,
	WORKSTATION,
	QUALITY_IMPACT,
	CALIBRATION,
	CALIBRATION_DAYS,
	MAINTENANCE,
	MAINTENANCE_DAYS,
	QUALIFICATION,
)
# ERPNext's asset statuses in which the equipment is not at hand to use.
NOT_AT_HAND = ("Sold", "Scrapped", "In Maintenance", "Out of Order", "Capitalized", "Cancelled")

CUSTOM_FIELDS = {
	DOCTYPE: [
		{
			"fieldname": "mds_equipment_section",
			"label": "Equipment",
			"fieldtype": "Section Break",
			"insert_after": "item_name",
			"collapsible": 0,
		},
		{
			"fieldname": TAG,
			"label": "Equipment Tag",
			"fieldtype": "Data",
			"insert_after": "mds_equipment_section",
			"no_copy": 1,  # a copy is another piece of equipment
			"search_index": 1,  # unique among equipment not cancelled (guard_asset)
			"allow_on_submit": 1,
			"in_standard_filter": 1,
			"description": "The plant's own number on the equipment's label, one per piece of equipment.",
		},
		{
			"fieldname": CLASS,
			"allow_on_submit": 1,
			"label": "Equipment Class",
			"fieldtype": "Select",
			"options": "\n" + "\n".join(CLASSES),
			"insert_after": TAG,
			"in_standard_filter": 1,
		},
		{
			"fieldname": MAKER,
			"allow_on_submit": 1,
			"label": "Manufacturer",
			"fieldtype": "Data",
			"insert_after": CLASS,
		},
		{
			"fieldname": MODEL,
			"allow_on_submit": 1,
			"label": "Model",
			"fieldtype": "Data",
			"insert_after": MAKER,
		},
		{
			"fieldname": SERIAL,
			"allow_on_submit": 1,
			"label": "Manufacturer's Serial Number",
			"fieldtype": "Data",
			"insert_after": MODEL,
			"no_copy": 1,
		},
		{"fieldname": "mds_equipment_column", "fieldtype": "Column Break", "insert_after": SERIAL},
		{
			"fieldname": WORKSTATION,
			"allow_on_submit": 1,
			"label": "Workstation",
			"fieldtype": "Link",
			"options": "Workstation",
			"insert_after": "mds_equipment_column",
			"description": "For production equipment: the workstation it is part of.",
		},
		{
			"fieldname": QUALITY_IMPACT,
			"allow_on_submit": 1,
			"label": "Affects Product Quality",
			"fieldtype": "Check",
			"insert_after": WORKSTATION,
			"description": "It makes, measures, tests or sterilizes product, or controls a condition the product needs.",
		},
		{
			"fieldname": STATUS,
			"label": "Equipment Status",
			"fieldtype": "Select",
			"options": "\n".join(STATUSES),
			"default": IN_SERVICE,
			"insert_after": QUALITY_IMPACT,
			"allow_on_submit": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "mds_equipment_care_section",
			"label": "Calibration, Maintenance and Qualification",
			"fieldtype": "Section Break",
			"insert_after": STATUS,
		},
		{
			"fieldname": CALIBRATION,
			"allow_on_submit": 1,
			"label": "Needs Calibration",
			"fieldtype": "Check",
			"insert_after": "mds_equipment_care_section",
		},
		{
			"fieldname": CALIBRATION_DAYS,
			"allow_on_submit": 1,
			"label": "Calibration Interval (days)",
			"fieldtype": "Int",
			"insert_after": CALIBRATION,
			"depends_on": f"eval:doc.{CALIBRATION}",
			"non_negative": 1,
		},
		{
			"fieldname": MAINTENANCE,
			"allow_on_submit": 1,
			"label": "Needs Preventive Maintenance",
			"fieldtype": "Check",
			"insert_after": CALIBRATION_DAYS,
		},
		{
			"fieldname": MAINTENANCE_DAYS,
			"allow_on_submit": 1,
			"label": "Maintenance Interval (days)",
			"fieldtype": "Int",
			"insert_after": MAINTENANCE,
			"depends_on": f"eval:doc.{MAINTENANCE}",
			"non_negative": 1,
		},
		{
			"fieldname": QUALIFICATION,
			"allow_on_submit": 1,
			"label": "Needs Qualification (IQ, OQ, PQ)",
			"fieldtype": "Check",
			"insert_after": MAINTENANCE_DAYS,
		},
		{
			"fieldname": "mds_equipment_approval_column",
			"fieldtype": "Column Break",
			"insert_after": QUALIFICATION,
		},
		{
			"fieldname": APPROVAL_STATUS,
			"label": "Approval by QA",
			"fieldtype": "Select",
			"options": f"{DRAFT}\n{APPROVED}",
			"default": DRAFT,
			"insert_after": "mds_equipment_approval_column",
			"read_only": 1,
			"no_copy": 1,
			"allow_on_submit": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": APPROVED_BY,
			"label": "Approved By",
			"fieldtype": "Link",
			"options": "User",
			"insert_after": APPROVAL_STATUS,
			"read_only": 1,
			"no_copy": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": APPROVED_AT,
			"label": "Approved At",
			"fieldtype": "Datetime",
			"insert_after": APPROVED_BY,
			"read_only": 1,
			"no_copy": 1,
			"allow_on_submit": 1,
		},
	]
}


def apply_equipment_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def is_qa(user: str | None = None) -> bool:
	return QA_ROLE in frappe.get_roles(user or frappe.session.user)


def require(condition: object, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)


def approved(asset: str | Document) -> bool:
	status = (
		asset.get(APPROVAL_STATUS)
		if isinstance(asset, Document)
		else frappe.db.get_value(DOCTYPE, asset, APPROVAL_STATUS)
	)
	return status == APPROVED


def missing_for_approval(doc: Document) -> list[str]:
	"""What the equipment still lacks for QA to approve it, in words the form shows."""
	missing = [
		label
		for field, label in (
			(TAG, _("its equipment tag")),
			(CLASS, _("its class")),
			(MAKER, _("its manufacturer")),
			(MODEL, _("its model")),
			(SERIAL, _("its manufacturer's serial number")),
		)
		if not (doc.get(field) or "").strip()
	]
	if doc.get(CLASS) and doc.get(CLASS) not in CLASSES:
		missing.append(_("a class of the list"))
	if not doc.get("location") and not doc.get(WORKSTATION):
		missing.append(_("where it is used (its location or workstation)"))
	for needs, days, label in (
		(CALIBRATION, CALIBRATION_DAYS, _("its calibration interval")),
		(MAINTENANCE, MAINTENANCE_DAYS, _("its maintenance interval")),
	):
		if cint(doc.get(needs)) and cint(doc.get(days)) < 1:
			missing.append(label)
	if doc.get(CLASS) == "Measuring instrument" and not cint(doc.get(CALIBRATION)):
		missing.append(_("calibration (a measuring instrument is calibrated)"))
	return missing


# --- The signed approval ---------------------------------------------------------------------


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA's signed approval of a piece of equipment (compliance.signing.SIGNING_ACTIONS)."""
	frappe.db.get_value(DOCTYPE, doc.name, "name", for_update=True)  # one approval at a time
	doc = frappe.get_doc(DOCTYPE, doc.name)
	doc.check_permission("read")
	require(is_qa(), _("Only QA approves equipment."), frappe.PermissionError)
	require(not approved(doc), _("{0} is approved already.").format(doc.name))
	require(doc.docstatus != 2, _("{0} is cancelled; it is not approved.").format(doc.name))
	require(doc.get(STATUS) != RETIRED, _("{0} is retired; it is not approved.").format(doc.name))
	require(reason, _("Say why the equipment is approved."))
	missing = missing_for_approval(doc)
	require(not missing, _("{0} cannot be approved yet; it lacks {1}.").format(doc.name, "; ".join(missing)))
	doc.set(APPROVAL_STATUS, APPROVED)
	doc.set(APPROVED_BY, frappe.session.user)
	doc.set(APPROVED_AT, now_datetime())
	sign(doc, "Approved", "Approve", password, reason)
	doc.flags.equipment_approval = True
	doc.save()


def signed_content(doc: Document) -> dict:
	"""What QA's signature binds (compliance.esign.SIGNED_CONTENT): what was approved; not the
	asset's accounting, its status from day to day, or ERPNext's own bookkeeping."""
	content = {}
	for field in (*CONTROLLED_FIELDS, "item_code", "asset_name"):  # not the location (moves)
		df = doc.meta.get_field(field)
		value = doc.get(field)
		content[field] = cint(value) if df and df.fieldtype in ("Check", "Int") else (value or None)
	return content


# --- The asset's rules ----------------------------------------------------------------------


def guard_asset(doc: Document, method: str | None = None) -> None:
	"""Asset validate and before_update_after_submit: the approval is the signed act's alone; a new
	or copied asset is in draft. The tag is the equipment's own. Retired is final. What approval
	needs stays stated once the equipment is approved."""
	if not frappe.get_meta(DOCTYPE).has_field(APPROVAL_STATUS):
		return  # the fields arrive with apply_configuration
	before = None if doc.is_new() else doc.get_doc_before_save()
	if not doc.flags.get(APPROVAL_FLAG):
		if before is None:
			doc.set(APPROVAL_STATUS, DRAFT)
			doc.set(APPROVED_BY, None)
			doc.set(APPROVED_AT, None)
		else:
			for field in APPROVAL_FIELDS:
				if not same(before.get(field), doc.get(field), doc.meta.get_field(field).fieldtype):
					frappe.throw(
						_("The approval of {0} is given only by QA's signed approval.").format(doc.name),
						frappe.PermissionError,
					)
	if doc.get(STATUS) and doc.get(STATUS) not in STATUSES:
		frappe.throw(_("{0} is not an equipment status.").format(doc.get(STATUS)))
	if before is not None and before.get(STATUS) == RETIRED and doc.get(STATUS) != RETIRED:
		frappe.throw(_("{0} is retired; retired equipment is not put back into use.").format(doc.name))
	tag = (doc.get(TAG) or "").strip() or None
	doc.set(TAG, tag)
	if tag and (
		other := frappe.db.get_value(
			DOCTYPE,
			{TAG: tag, "docstatus": ["!=", 2], "name": ["not in", [doc.name, doc.get("amended_from") or ""]]},
		)
	):
		frappe.throw(
			_("Equipment tag {0} is on {1} already; each piece of equipment has its own.").format(tag, other)
		)
	for field in (CALIBRATION_DAYS, MAINTENANCE_DAYS):
		if cint(doc.get(field)) < 0:
			frappe.throw(_("{0} is a number of days.").format(_(doc.meta.get_label(field))))
	if approved(doc) and (missing := missing_for_approval(doc)):
		frappe.throw(
			_("{0} is approved equipment and keeps what its approval needed; it lacks {1}.").format(
				doc.name, "; ".join(missing)
			)
		)


def usable(asset: str) -> frappe._dict:
	"""Whether a piece of equipment may be used now, and its name and status for a message."""
	row = frappe.db.get_value(
		DOCTYPE, asset, ["name", APPROVAL_STATUS, STATUS, "docstatus", "status"], as_dict=True
	)
	if not row:
		return frappe._dict(ok=False, why=_("it does not exist"))
	if row.docstatus == 2:
		return frappe._dict(ok=False, why=_("it is cancelled"))
	if row.get("status") in NOT_AT_HAND:
		return frappe._dict(ok=False, why=_("it is {0}").format(_(row.get("status")).lower()))
	if row.get(APPROVAL_STATUS) != APPROVED:
		return frappe._dict(ok=False, why=_("QA has not approved it"))
	if row.get(STATUS) != IN_SERVICE:
		return frappe._dict(ok=False, why=_("it is {0}").format(_(row.get(STATUS) or OUT_OF_SERVICE).lower()))
	return frappe._dict(ok=True)


def guard_inspection_instruments(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection validate: a reading's instrument is approved equipment in service."""
	if doc.docstatus == 2:
		return
	for instrument in sorted({row.get("mds_instrument") for row in doc.get("readings") or []} - {None, ""}):
		state = usable(instrument)
		if not state.ok:
			frappe.throw(
				_("Instrument {0} is not used for an inspection: {1}.").format(instrument, state.why),
				title=_("Equipment not usable"),
			)


def asset_onload(doc: Document, method: str | None = None) -> None:
	"""Asset onload: what approval still needs, for the form."""
	doc.set_onload(
		"equipment_approval",
		{
			"approved": approved(doc),
			"missing": [] if approved(doc) else missing_for_approval(doc),
			# Not offered for approval: approve() refuses these.
			"approvable": doc.docstatus != 2 and doc.get(STATUS) != RETIRED,
		},
	)
