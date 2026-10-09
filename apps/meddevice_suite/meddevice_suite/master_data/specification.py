"""Controlled specifications (module 6; URS-MST-002; ISO 13485 7.1, 8.2.6).

An item's inspection template is its specification for one inspection stage: ERPNext's
Quality Inspection Template, with the item, the stage (Incoming, In Process, Final) and the
sample size, and for each parameter the method and the limits (a minimum and/or maximum for
a measurement, the accepted value otherwise). One specification is in force per item and
stage.

- An inspection is made against the item's specification for its stage, with the
  specification's parameters and limits as they stand, and samples at least its sample size.
- A specification in use, one a submitted inspection was made against, changes only under
  an approved change request that lists it (module 36). The request is named for each change
  and recorded as the one the specification was last changed under; the audit trail keeps
  the values the change replaced.
- A template made before this rule becomes a specification once QA completes it; completing
  it is its first issue and needs no change request (as the first revision of a document).
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import cint, flt

from meddevice_suite.compliance.audit import same
from meddevice_suite.qms.change_control import require_approved_change

DOCTYPE = "Quality Inspection Template"
PARAMETERS = "item_quality_inspection_parameter"
INCOMING, IN_PROCESS, FINAL = "Incoming", "In Process", "Final"
# ERPNext's inspection type -> the specification's stage
STAGE_OF_INSPECTION = {"Incoming": INCOMING, "In Process": IN_PROCESS, "Outgoing": FINAL}
# What a specification states, with each field's type; a change to any of these is a change
# of the specification.
CONTROLLED_FIELDS = {"mds_item": "Link", "mds_stage": "Select", "mds_sample_size": "Int"}
# The acceptance criteria a reading carries from its specification.
CRITERIA = {
	"numeric": "Check",
	"min_value": "Float",
	"max_value": "Float",
	"value": "Data",
	"formula_based_criteria": "Check",
	"acceptance_formula": "Code",
}
CONTROLLED_PARAMETER_FIELDS = {"specification": "Link", "mds_method": "Data", **CRITERIA}
CUSTOM_FIELDS = {
	DOCTYPE: [
		{
			"fieldname": "mds_item",
			"label": "Item",
			"fieldtype": "Link",
			"options": "Item",
			"insert_after": "quality_inspection_template_name",
			"in_list_view": 1,
			"in_standard_filter": 1,
			"search_index": 1,
			"description": "The item this specification is for.",
		},
		{
			"fieldname": "mds_stage",
			"label": "Inspection Stage",
			"fieldtype": "Select",
			"options": f"\n{INCOMING}\n{IN_PROCESS}\n{FINAL}",
			"insert_after": "mds_item",
			"in_list_view": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "mds_sample_size",
			"label": "Sample Size",
			"fieldtype": "Int",
			"insert_after": "mds_stage",
			"description": "The fewest units an inspection examines.",
		},
		{
			"fieldname": "mds_change_request",
			"label": "Change Request for This Change",
			"fieldtype": "Link",
			"options": "Change Request",
			"insert_after": "mds_sample_size",
			"no_copy": 1,
			"description": "Once the specification is in use: the approved change request this change is made under. Named for each change.",
		},
		{
			"fieldname": "mds_changed_under",
			"label": "Last Changed Under",
			"fieldtype": "Link",
			"options": "Change Request",
			"insert_after": "mds_change_request",
			"read_only": 1,
			"no_copy": 1,
		},
	],
	"Item Quality Inspection Parameter": [
		{
			"fieldname": "mds_method",
			"label": "Method",
			"fieldtype": "Data",
			"insert_after": "specification",
			"in_list_view": 1,
			"columns": 2,
			"description": "How the parameter is checked: the method or standard.",
		}
	],
}


def apply_specification_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


# --- The specification -----------------------------------------------------------------------


def missing_from(doc: Document) -> list[str]:
	"""What the template does not yet state to be a specification."""
	missing = [
		label
		for field, label in (("mds_item", _("the item")), ("mds_stage", _("the inspection stage")))
		if not doc.get(field)
	]
	if cint(doc.get("mds_sample_size")) < 1:
		missing.append(_("the sample size"))
	rows = doc.get(PARAMETERS) or []
	if not rows:
		missing.append(_("its parameters"))
	for row in rows:
		if not (row.get("mds_method") or "").strip():
			missing.append(_("the method of {0}").format(row.specification))
		if cint(row.formula_based_criteria):
			if not (row.get("acceptance_formula") or "").strip():
				missing.append(_("the acceptance formula of {0}").format(row.specification))
		elif cint(row.numeric):
			# A limit left blank is stored as 0: a measurement with neither limit set has none.
			if not flt(row.get("min_value")) and not flt(row.get("max_value")):
				missing.append(_("the limits of {0}").format(row.specification))
		elif not (row.get("value") or "").strip():
			missing.append(_("the accepted value of {0}").format(row.specification))
	return missing


def changed(before: Document, after: Document) -> bool:
	"""Whether the specification states something else, compared as stored (the desk sends
	numbers and dates as text)."""
	if any(not same(before.get(f), after.get(f), t) for f, t in CONTROLLED_FIELDS.items()):
		return True
	rows_before, rows_after = before.get(PARAMETERS) or [], after.get(PARAMETERS) or []
	return len(rows_before) != len(rows_after) or any(
		not same(old.get(f), new.get(f), t)
		for old, new in zip(rows_before, rows_after, strict=True)
		for f, t in CONTROLLED_PARAMETER_FIELDS.items()
	)


def in_use(name: str) -> bool:
	return bool(frappe.db.exists("Quality Inspection", {"quality_inspection_template": name, "docstatus": 1}))


def guard_template(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection Template validate: a specification states everything an inspection
	needs, is the only one for its item and stage, and once in use changes only under an
	approved change request that lists it."""
	before = None if doc.is_new() else doc.get_doc_before_save()
	was_specification = before is not None and not missing_from(before)
	missing = missing_from(doc)
	if missing and (was_specification or any(doc.get(field) for field in CONTROLLED_FIELDS)):
		frappe.throw(
			_("Specification {0} does not yet state {1}.").format(doc.name, ", ".join(missing)),
			title=_("Specification incomplete"),
		)
	if not missing:
		other = frappe.db.get_value(
			DOCTYPE,
			{"mds_item": doc.mds_item, "mds_stage": doc.mds_stage, "name": ["!=", doc.name]},
		)
		if other:
			frappe.throw(
				_("{0} already holds the {1} specification of {2}; change that one instead.").format(
					other, _(doc.mds_stage).lower(), doc.mds_item
				),
				title=_("One specification per item and stage"),
			)
	if before is not None and not same(before.get("mds_changed_under"), doc.get("mds_changed_under"), "Link"):
		frappe.throw(_("{0} records the change request a change was made under itself.").format(doc.name))
	request, doc.mds_change_request = doc.get("mds_change_request"), None  # named afresh for each change
	if was_specification and changed(before, doc) and in_use(doc.name):
		if not request:
			frappe.throw(
				_(
					"Specification {0} is in use: name the approved change request that lists it to change it."
				).format(doc.name),
				title=_("Change control"),
			)
		require_approved_change(request, DOCTYPE, doc.name)
		doc.mds_changed_under = request


def protect_template(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection Template on_trash: a specification in use is a record of what was
	inspected against; it is never deleted."""
	if in_use(doc.name):
		frappe.throw(
			_("Specification {0} is in use and is never deleted.").format(doc.name), frappe.PermissionError
		)


def specification_for(item: str, stage: str) -> str | None:
	"""The item's specification for the stage, if QA has registered a complete one."""
	name = frappe.db.get_value(DOCTYPE, {"mds_item": item, "mds_stage": stage})
	return name if name and not missing_from(frappe.get_doc(DOCTYPE, name)) else None


@frappe.whitelist()
def specification_for_inspection(item_code: str, inspection_type: str) -> str | None:
	"""The inspection form's lookup of the specification it is made against."""
	frappe.has_permission("Quality Inspection", "read", throw=True)
	stage = STAGE_OF_INSPECTION.get(inspection_type)
	return specification_for(item_code, stage) if stage else None


# --- The inspection --------------------------------------------------------------------------


def choose_specification(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection before_validate: the inspection is made against the item's
	specification for its stage. Readings not yet taken are laid out from it again."""
	stage = STAGE_OF_INSPECTION.get(doc.inspection_type)
	if not stage or not doc.item_code or doc.docstatus == 2:
		return
	specification = specification_for(doc.item_code, stage)
	if not specification:
		frappe.throw(
			_("Item {0} has no {1} specification; QA registers it before the item is inspected.").format(
				doc.item_code, _(stage).lower()
			),
			title=_("No specification"),
		)
	if doc.quality_inspection_template == specification:
		return
	if not doc.quality_inspection_template:
		# Readings brought without a template are checked against the specification next.
		doc.quality_inspection_template = specification
		return
	if any(taken(reading) for reading in doc.readings):
		frappe.throw(
			_("Inspect {0} against its {1} specification {2}.").format(
				doc.item_code, _(stage).lower(), specification
			),
			title=_("Wrong specification"),
		)
	doc.quality_inspection_template = specification
	doc.set("readings", [])  # ERPNext lays them out from the specification in its validate


def taken(reading: Document) -> bool:
	return bool(
		(reading.get("reading_value") or "").strip()
		or any(reading.get(f"reading_{i}") not in (None, "") for i in range(1, 11))
	)


def check_against_specification(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection validate, after ERPNext lays out the readings: one reading per
	parameter of the specification, each with the specification's limits as they stand."""
	if (
		doc.docstatus == 2
		or doc.inspection_type not in STAGE_OF_INSPECTION
		or not doc.quality_inspection_template
	):
		return
	parameters = {
		row.specification: row
		for row in frappe.get_doc(DOCTYPE, doc.quality_inspection_template).get(PARAMETERS)
	}
	if doc.inspection_type == "In Process" and doc.reference_type == "Job Card":
		# ERPNext's validate copies the limits of the item master's own template into a job
		# card's readings (quality_inspection.py); the specification's limits apply instead, and
		# the readings are judged against them again.
		for reading in doc.readings:
			if reading.specification in parameters:
				reading.update({f: parameters[reading.specification].get(f) for f in CRITERIA})
		doc.inspect_and_set_status()
	read = [reading.specification for reading in doc.readings]
	if sorted(read) != sorted(parameters):
		frappe.throw(
			_("An inspection against {0} reads each of its parameters once: {1}.").format(
				doc.quality_inspection_template, ", ".join(parameters)
			),
			title=_("Readings do not match the specification"),
		)
	for reading in doc.readings:
		parameter = parameters[reading.specification]
		if any(not same(reading.get(f), parameter.get(f), t) for f, t in CRITERIA.items()):
			frappe.throw(
				_(
					"The limits of {0} come from specification {1} and are not changed on an inspection."
				).format(reading.specification, doc.quality_inspection_template),
				title=_("Limits changed"),
			)


def check_sample_size(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection before_submit: at least the specification's sample size."""
	if doc.inspection_type not in STAGE_OF_INSPECTION or not doc.quality_inspection_template:
		return
	required = cint(frappe.db.get_value(DOCTYPE, doc.quality_inspection_template, "mds_sample_size"))
	if flt(doc.sample_size) < required:
		frappe.throw(
			_("Specification {0} samples at least {1}; this inspection samples {2}.").format(
				doc.quality_inspection_template,
				required,
				frappe.format_value(doc.sample_size, {"fieldtype": "Float"}),
			),
			title=_("Sample too small"),
		)
