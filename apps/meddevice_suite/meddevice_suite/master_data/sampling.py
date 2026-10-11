"""Sampling plans (module 6; URS-MST-009; ISO 2859-1; ISO 13485 7.4.3, 8.2.6).

Each specification (master_data/specification.py) states how its stage is sampled:

- a fixed sample size, as before: the inspection samples at least that many and its readings
  decide it;
- every unit of the lot; or
- ISO 2859-1 single sampling for normal inspection at an inspection level and an AQL.

For ISO 2859-1 the inspection's plan follows from the size of the lot inspected by Table 1 (the
code letter) and Table 2-A (the sample size and the accept and reject numbers). An arrow in Table
2-A points to the first plan below or above it, and that plan's code letter gives the sample
size. When the sample would reach the lot, every unit is inspected (the table's footnote).

The lot is counted by the system, not typed: the lot's quantity in stock when it is inspected, or
the quantity of the job card an in-process inspection is made on. The inspector records the
nonconforming units found. The lot is accepted only when they do not exceed the accept number,
and rejected when they reach the reject number. A reading out of its limits is a nonconforming
unit, so the count is never below what the readings show. Under a plan that accepts with a
nonconforming unit, that unit is still nonconforming and is handled as one; the decision is about
the lot.

The tables are master_data/iso2859_1.json, each cell confirmed against at least two independent
sources (see its "verified" note); the switching rules to tightened and reduced inspection are not
in scope (decided 2026-10-11). An inspection level and an AQL are a specification's data, chosen
by QA under change control; the tables are the standard's, not settings.
"""

import json
import os
from dataclasses import dataclass
from functools import cache

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

FIXED, EVERY_UNIT, ISO_2859_1 = "Fixed sample size", "Every unit", "ISO 2859-1"
METHODS = (FIXED, EVERY_UNIT, ISO_2859_1)
LEVELS = ("S-1", "S-2", "S-3", "S-4", "I", "II", "III")
LETTERS = "ABCDEFGHJKLMNPQR"
UP, DOWN = "up", "down"
# On the specification (Quality Inspection Template)
METHOD, LEVEL, AQL = "mds_sampling", "mds_inspection_level", "mds_aql"
# On the inspection (Quality Inspection), set by the plan
LOT_SIZE, CODE_LETTER, ACCEPT, REJECT, EVERY = (
	"mds_lot_size",
	"mds_code_letter",
	"mds_accept_number",
	"mds_reject_number",
	"mds_every_unit",
)
PLAN_FIELDS = (LOT_SIZE, CODE_LETTER, ACCEPT, REJECT, EVERY)
NONCONFORMING = "mds_nonconforming"


@dataclass(frozen=True)
class Plan:
	sample_size: int
	accept: int
	reject: int
	code_letter: str | None = None  # the letter whose sample size is used, after the arrows
	every_unit: bool = False


@cache
def tables() -> dict:
	with open(os.path.join(os.path.dirname(__file__), "iso2859_1.json")) as f:
		return json.load(f)


def aqls() -> tuple[str, ...]:
	return tuple(tables()["aqls"])


def code_letter(lot_size: int, level: str) -> str:
	"""ISO 2859-1 Table 1: the sample size code letter of a lot size at an inspection level."""
	if level not in LEVELS:
		frappe.throw(_("{0} is not an inspection level of ISO 2859-1.").format(level))
	if lot_size < 2:
		frappe.throw(
			_("A lot sampled by ISO 2859-1 holds at least 2 units; this one holds {0}.").format(lot_size)
		)
	for row in tables()["code_letters"]:
		if lot_size >= row["lot_min"] and (row["lot_max"] is None or lot_size <= row["lot_max"]):
			return row[level]
	raise AssertionError("ISO 2859-1 Table 1 covers every lot size from 2")  # the table is open-ended


def table_plan(letter: str, aql: str) -> Plan:
	"""ISO 2859-1 Table 2-A: the plan of a code letter at an AQL, its arrows followed to the first
	plan below or above; the new code letter gives the sample size."""
	if aql not in aqls():
		frappe.throw(_("{0} is not an AQL of ISO 2859-1.").format(aql))
	column, row = aqls().index(aql), LETTERS.index(letter)
	cells = tables()["normal_single"]
	while True:
		cell = cells[LETTERS[row]][column]
		if cell == DOWN:
			row += 1
		elif cell == UP:
			row -= 1
		else:
			used = LETTERS[row]
			return Plan(tables()["sample_sizes"][used], cell[0], cell[1], used)
		if not 0 <= row < len(LETTERS):
			raise AssertionError(f"an arrow of ISO 2859-1 Table 2-A leads out of the table at {letter} {aql}")


def whole_lot(lot_size: float) -> int:
	"""A lot sampled by ISO 2859-1 is counted in units."""
	if flt(lot_size) != cint(lot_size):
		frappe.throw(
			_("ISO 2859-1 samples units; a lot of {0} is not a whole number of units.").format(
				frappe.format_value(lot_size, {"fieldtype": "Float"})
			)
		)
	return cint(lot_size)


def plan_for(lot_size: float, level: str, aql: str) -> Plan:
	"""The single sampling plan for normal inspection of a lot; every unit when the sample would
	reach the lot (Table 2-A's footnote), a lot of one unit included."""
	lot = whole_lot(lot_size)
	if lot == 1:
		return Plan(1, 0, 1, every_unit=True)
	plan = table_plan(code_letter(lot, level), aql)
	if plan.sample_size >= lot:
		return Plan(lot, plan.accept, plan.reject, plan.code_letter, every_unit=True)
	return plan


# --- The inspection --------------------------------------------------------------------------


def under_plan(doc: Document) -> frappe._dict | None:
	"""The specification's sampling, when the inspection is under a plan (every unit or ISO
	2859-1); None for a fixed sample size. Read from the specification, never from the record."""
	template = doc.get("quality_inspection_template")
	if not template:
		return None
	spec = frappe.db.get_value("Quality Inspection Template", template, [METHOD, LEVEL, AQL], as_dict=True)
	return spec if spec and spec.get(METHOD) in (EVERY_UNIT, ISO_2859_1) else None


def receipt_line(doc: Document) -> frappe._dict | None:
	"""The goods receipt line that brought an incoming lot in."""
	if doc.get("inspection_type") != "Incoming" or not doc.get("batch_no"):
		return None
	from meddevice_suite.lot_control.receipt import receipt_line_of  # module 16 builds on module 6

	return receipt_line_of(doc.batch_no)


def lot_size_of(doc: Document) -> float | None:
	"""The size of the lot an inspection is of, as the system counts it: for a lot a supplier
	delivered, the quantity received; for an in-process inspection on a job card, the job card's
	quantity; otherwise the lot's quantity in stock, reserved or not, in every store."""
	if doc.get("reference_type") == "Job Card" and doc.get("reference_name"):
		card = frappe.db.get_value(
			"Job Card", doc.reference_name, ["production_item", "for_quantity"], as_dict=True
		)
		if not card or card.production_item != doc.item_code:
			frappe.throw(
				_("Job card {0} does not make {1}; inspect it on its own job card.").format(
					doc.reference_name, doc.item_code
				)
			)
		return flt(card.for_quantity)
	if not doc.get("batch_no"):
		return None
	line = receipt_line(doc)
	if line is not None:
		return flt(line.quantity)
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	held = get_batch_qty(
		batch_no=doc.batch_no, item_code=doc.item_code, for_stock_levels=True, ignore_reserved_stock=True
	)
	if isinstance(held, (int, float)):
		return flt(held)
	return sum(flt(row.get("qty")) for row in held or [])  # by store, as ERPNext gives it


def plan_of(doc: Document, spec: frappe._dict) -> Plan:
	"""The plan of an inspection under its specification's sampling, from the lot as it stands. A
	lot from a conditionally approved supplier is inspected in full (module 4): every unit, as the
	specification's Every unit, accepting none nonconforming."""
	lot = lot_size_of(doc)
	if not lot or lot <= 0:
		frappe.throw(
			_(
				"Specification {0} samples by the size of the lot; name the lot (or the job card) "
				"this inspection is of, with its quantity."
			).format(doc.quality_inspection_template),
			title=_("Lot not known"),
		)
	line = receipt_line(doc)
	if spec.get(METHOD) == EVERY_UNIT or (line is not None and line.full_inspection):
		return Plan(whole_lot(lot), 0, 1, every_unit=True)
	return plan_for(lot, spec.get(LEVEL), spec.get(AQL))


def apply_plan(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection before_validate, after the specification is chosen: the plan follows from
	the specification and the lot on every save and again at submit, from the system's own records,
	whatever the record was sent with; once submitted it stays."""
	if doc.docstatus == 2 or (doc.docstatus == 1 and getattr(doc, "_action", None) != "submit"):
		return
	spec = under_plan(doc)
	if not spec:
		# A fixed sample size: the readings decide. Numbers are stored as 0 when not set.
		doc.update({LOT_SIZE: 0, CODE_LETTER: None, ACCEPT: 0, REJECT: 0, EVERY: 0})
		return
	plan = plan_of(doc, spec)
	doc.update(
		{
			LOT_SIZE: lot_size_of(doc),
			CODE_LETTER: plan.code_letter,
			ACCEPT: plan.accept,
			REJECT: plan.reject,
			EVERY: int(plan.every_unit),
		}
	)
	if flt(doc.sample_size) < plan.sample_size:
		doc.sample_size = plan.sample_size  # the plan's sample size; the inspector may sample more


def readings_out_of_limits(doc: Document) -> int:
	"""The fewest nonconforming units the readings show: the most values out of limits that any one
	parameter has, each value being one unit's, judged as ERPNext judges them; a parameter judged
	another way (a value, a formula) that failed counts one."""
	from erpnext.stock.doctype.quality_inspection.quality_inspection import (
		parse_float,  # as ERPNext reads them
	)

	worst = 0
	for reading in doc.get("readings") or []:
		if reading.get("status") != "Rejected":
			continue
		values = [
			reading.get(f"reading_{i}") for i in range(1, 11) if (reading.get(f"reading_{i}") or "").strip()
		]
		if cint(reading.get("numeric")) and not cint(reading.get("formula_based_criteria")) and values:
			low, high = flt(reading.get("min_value")), flt(reading.get("max_value"))
			bad = sum(1 for value in values if not low <= parse_float(value) <= high)
			worst = max(worst, bad or 1)
		else:
			worst = max(worst, 1)
	return worst


def judge(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection validate, after ERPNext judges the readings: under a plan, the lot is
	decided by the nonconforming units found against its accept and reject numbers. An inspector's
	own Rejected (manual inspection) stands; nothing accepts a lot beyond the plan."""
	if doc.docstatus == 2 or not under_plan(doc):
		return
	found = cint(doc.get(NONCONFORMING))
	if found < 0 or found > flt(doc.sample_size):
		frappe.throw(
			_("The nonconforming units found are between 0 and the {0} units sampled.").format(
				frappe.format_value(doc.sample_size, {"fieldtype": "Float"})
			)
		)
	shown = readings_out_of_limits(doc)
	if found < shown:
		frappe.throw(
			_("The readings show at least {0} nonconforming units; record them as found.").format(shown),
			title=_("Nonconforming units"),
		)
	if found >= cint(doc.get(REJECT)):
		doc.status = "Rejected"
	elif not (cint(doc.get("manual_inspection")) and doc.status == "Rejected"):
		doc.status = "Accepted"
	if (
		getattr(doc, "_action", None) == "submit"
		and doc.status == "Accepted"
		and found
		and not (doc.get("remarks") or "").strip()
	):
		frappe.throw(
			_(
				"The lot is accepted with {0} nonconforming units found; say in the remarks how they were "
				"set apart, as nonconforming product."
			).format(found),
			title=_("Nonconforming units"),
		)


def check_plan_on_submit(doc: Document) -> None:
	"""Quality Inspection before_submit: the plan recorded is the plan of the lot as the system
	counts it, and the sample at least its size."""
	spec = under_plan(doc)
	if not spec:
		return
	plan = plan_of(doc, spec)
	recorded = (cint(doc.get(ACCEPT)), cint(doc.get(REJECT)), flt(doc.get(LOT_SIZE)))
	if recorded != (plan.accept, plan.reject, flt(lot_size_of(doc))):
		frappe.throw(_("The sampling plan of this inspection is set by the system; save it again."))
	if flt(doc.sample_size) < plan.sample_size:
		frappe.throw(
			_(
				"The lot of {0} is sampled with {1} units by specification {2}; this inspection samples {3}."
			).format(
				frappe.format_value(lot_size_of(doc), {"fieldtype": "Float"}),
				plan.sample_size,
				doc.quality_inspection_template,
				frappe.format_value(doc.sample_size, {"fieldtype": "Float"}),
			),
			title=_("Sample too small"),
		)
