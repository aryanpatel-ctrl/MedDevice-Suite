"""Approve, by migration, each specification an inspection was already made against (URS-MST-014;
foundation hardening plan D-33, decided 2026-10-14).

From this release an inspection is made only against a specification QA has approved with a
signature. A specification a submitted inspection was made against was in use before the rule:
left in draft, it would stop every inspection of its item, and so receiving and production, until
QA had signed each one. It is approved by migration instead, naming no approver, with the reason
below in the audit trail; its form says it was approved by migration, and QA approves it with a
signature at its next change, which returns it to draft. Every other specification stays in draft
until QA approves it. A specification approved by this patch once is never approved by it again,
so running it again, after one of them was changed, changes nothing. The fields are made here as
well, because patches run before the after-migrate configuration.
"""

import json

import frappe
from frappe.utils import now_datetime

from meddevice_suite.compliance.audit import record
from meddevice_suite.master_data import master_approval
from meddevice_suite.master_data.specification import DOCTYPE, apply_specification_fields

REASON = (
	"Migration: in use by a submitted inspection before specifications were approved by QA (plan D-33); "
	"QA approves it with a signature at its next change"
)


def execute() -> None:
	apply_specification_fields()
	in_use = frappe.get_all(
		"Quality Inspection",
		filters={"docstatus": 1, "quality_inspection_template": ["is", "set"]},
		pluck="quality_inspection_template",
		distinct=True,
	)
	if not in_use:
		return
	approved_before = set(
		frappe.get_all(
			"Audit Event",
			filters={"reference_doctype": DOCTYPE, "reference_name": ["in", in_use], "reason": REASON},
			pluck="reference_name",
		)
	)
	# A registered specification, stating its item and stage (module 6), still in draft; a template
	# that is not one is never inspected against now and is not approved.
	pending = frappe.get_all(
		DOCTYPE,
		filters={
			"name": ["in", sorted(set(in_use) - approved_before)],
			"mds_item": ["is", "set"],
			"mds_stage": ["is", "set"],
			master_approval.APPROVAL_STATUS: ["!=", master_approval.APPROVED],
		},
		fields=["name", master_approval.APPROVAL_STATUS],
	)
	for row in pending:  # one-off: the specifications of a site's items
		approved_at = now_datetime()
		values = {
			master_approval.APPROVAL_STATUS: master_approval.APPROVED,
			master_approval.APPROVED_AT: approved_at,
		}
		# A documented system change, recorded in the audit trail below with its reason.
		frappe.db.set_value(DOCTYPE, row.name, values, update_modified=False)
		changes = {
			"changed": [
				[
					master_approval.APPROVAL_STATUS,
					row.get(master_approval.APPROVAL_STATUS),
					master_approval.APPROVED,
				],
				[master_approval.APPROVED_AT, None, approved_at],
			]
		}
		record(DOCTYPE, row.name, "Updated", json.loads(frappe.as_json(changes)), reason=REASON)
