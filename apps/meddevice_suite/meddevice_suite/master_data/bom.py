"""Bill of materials (module 2; URS-MST-007; DFD D3 Item / BOM / Routing Master; ISO 13485 7.5.1,
4.2.3; 21 CFR 11.50, 11.70).

Production drafts a bill of materials; QA approves it with an electronic signature, which is the
only way it is submitted — for the Administrator too. QA approves it only when it is complete:
a QA-approved finished good or sub-assembly as its product (module 1), at least one component,
every component a QA-approved item with a quantity above zero, and a process-loss allowance
between 0 and 100 percent. The signature binds what was approved — the product, the quantity,
the components, the operations and the allowance — not the costs ERPNext keeps up to date.

QA signs the bill as it is stored: the signature is taken in the submit itself, after ERPNext's
validation has set what it sets (the unit, a component's default sub-bill) and after every other
check has passed, so a refused approval leaves no signature and a signature never disagrees with
the record. Both rules run at `before_validate`, which Frappe runs even for a record whose
validation a caller skips (`flags.ignore_validate`).

A work order is made only from an approved, active bill of its own product: ERPNext checks the
bill's product and that it is active, but skips its "must be submitted" check while tests run,
and accepts a bill that merely contains the product as a component; Production Plan makes its
work orders without validation. The rule is enforced here on every one of them.

Which bill is in force, and cancelling one, are under change control (URS-MST-001,
master_data/versioning.py). The acts are registered in compliance.signing.SIGNING_ACTIONS.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from meddevice_suite.compliance.checks import QA_ROLE, is_qa, require
from meddevice_suite.compliance.esign import sign

APPROVAL_FLAG = "bom_approval"

# What QA's signature binds: the recipe of the device (ISO 13485 4.2.3), not its costs, prices,
# website or warehouse defaults. A field ERPNext later renames fails test_bom, not silently.
SIGNED_FIELDS = (
	"item",
	"quantity",
	"uom",
	"company",
	"process_loss_percentage",
	"with_operations",
	"routing",
	"transfer_material_against",
	"allow_alternative_item",
	"inspection_required",
	"quality_inspection_template",
	"is_phantom_bom",
	"track_semi_finished_goods",
	"backflush_based_on",
)
SIGNED_ROW_FIELDS = {
	"items": (
		"item_code",
		"qty",
		"uom",
		"conversion_factor",
		"stock_qty",
		"bom_no",
		"operation",
		"allow_alternative_item",
		"include_item_in_manufacturing",
		"do_not_explode",
		"sourced_by_supplier",
	),
	"operations": (
		"sequence_id",
		"operation",
		"workstation",
		"workstation_type",
		"time_in_mins",
		"fixed_time",
		"batch_size",
		"bom_no",
		"finished_good",
		"finished_good_qty",
		"is_final_finished_good",
		"is_subcontracted",
		"quality_inspection_required",
	),
	"secondary_items": (
		"secondary_item_type",
		"item_code",
		"qty",
		"uom",
		"conversion_factor",
		"stock_qty",
		"process_loss_per",
	),
}
NUMERIC = frozenset(("Float", "Percent", "Currency"))
WHOLE = frozenset(("Int", "Check"))


def missing_for_approval(doc: Document) -> list[str]:
	"""What the bill still lacks for QA to approve it, in words the form shows."""
	from meddevice_suite.master_data import item_master  # module 2 builds on module 1

	rows = doc.get("items") or []
	codes = {doc.item, *(row.item_code for row in rows)} - {None, ""}
	items = (
		{
			item.name: item
			for item in frappe.get_all(
				"Item",
				filters={"name": ("in", list(codes))},
				fields=["name", item_master.ITEM_TYPE, item_master.APPROVAL_STATUS],
			)
		}
		if codes
		else {}
	)

	def approved(code: str) -> bool:
		return bool(code in items and items[code].get(item_master.APPROVAL_STATUS) == item_master.APPROVED)

	missing = []
	product = items.get(doc.item) or {}
	if product.get(item_master.ITEM_TYPE) not in (item_master.FINISHED_GOOD, item_master.SUB_ASSEMBLY):
		missing.append(_("a finished good or sub-assembly as its product"))
	elif not approved(doc.item):
		missing.append(_("its product approved by QA"))
	if not rows:
		missing.append(_("at least one component"))
	for row in rows:
		if flt(row.qty) <= 0:
			missing.append(_("a quantity above zero for {0}").format(row.item_code))
		elif not approved(row.item_code):
			missing.append(_("{0} approved by QA").format(row.item_code))
	if not 0 <= flt(doc.get("process_loss_percentage")) <= 100:
		missing.append(_("a process loss between 0 and 100 percent"))
	return missing


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA's signed approval of a bill of materials: its only submit.

	The signature is taken in the submit (`sign_approval`), on the bill as validated and stored;
	if anything refuses the submit, or the password, nothing of it is kept, whoever the caller
	(a request is rolled back anyway; the loaders and the console are not)."""
	frappe.db.get_value("BOM", doc.name, "name", for_update=True)  # one approval at a time
	doc = frappe.get_doc("BOM", doc.name)
	doc.check_permission("read")
	require(is_qa(), _("Only QA approves a bill of materials."), frappe.PermissionError)
	require(doc.docstatus == 0, _("{0} is approved already.").format(doc.name))
	require(reason, _("Say why the bill of materials is approved."))
	missing = missing_for_approval(doc)
	require(not missing, _("{0} cannot be approved yet; it lacks {1}.").format(doc.name, "; ".join(missing)))
	doc.flags.bom_approval = frappe._dict(password=password, reason=reason)  # the one submit let through
	savepoint = "bom_approval"
	frappe.db.savepoint(savepoint)
	try:
		doc.submit()
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	finally:
		doc.flags.bom_approval = None  # the password is not kept on the document


def approval_of(doc: Document) -> frappe._dict | bool | None:
	"""The approval `approve` is submitting, if any. Other modules' tests stand in for it with
	True (setup.test_stock.submit_bom_for_test), accepted only while tests run."""
	approval = doc.flags.get(APPROVAL_FLAG)
	if isinstance(approval, dict):
		return approval
	return approval is True and frappe.in_test


def sign_approval(doc: Document, method: str | None = None) -> None:
	"""BOM on_submit: QA's signature on the bill as it is now stored (see `approve`)."""
	approval = approval_of(doc)
	if isinstance(approval, dict):
		sign(doc, "Approved", "Approve", approval.password, approval.reason, permission="submit")


def only_by_approval(doc: Document, method: str | None = None) -> None:
	"""BOM before_validate: a bill of materials is submitted only by QA's signed approval.

	At `before_validate`, not `before_submit`: Frappe skips the latter for a record saved with
	`flags.ignore_validate`. A record inserted or saved straight as submitted is a submit too."""
	if getattr(doc, "_action", None) == "submit" and not approval_of(doc):
		frappe.throw(
			_("A bill of materials is submitted only by QA's signed approval (Approve BOM)."),
			frappe.PermissionError,
		)


def signed_content(doc: Document) -> dict:
	"""What QA's signature binds (compliance.esign.SIGNED_CONTENT): SIGNED_FIELDS of the bill and
	SIGNED_ROW_FIELDS of its components, operations and secondary items; not the costs ERPNext
	updates later."""
	content = values_of(doc, SIGNED_FIELDS)
	for table, fields in SIGNED_ROW_FIELDS.items():
		content[table] = [values_of(row, fields) for row in doc.get(table) or []]
	return content


def values_of(doc: Document, fields: tuple[str, ...]) -> dict:
	"""The fields' values, numbers in one form whether read from the form or the database."""
	values = {}
	for field in fields:
		df = doc.meta.get_field(field)
		value = doc.get(field)
		if df and df.fieldtype in NUMERIC:
			value = flt(value)
		elif df and df.fieldtype in WHOLE:
			value = cint(value)
		else:
			value = value or None
		values[field] = value
	return values


def default_bom(item: str) -> str | None:
	"""The item's bill of materials in force: approved by QA (submitted), active and the default."""
	return frappe.db.get_value("BOM", {"item": item, "docstatus": 1, "is_active": 1, "is_default": 1})


def bom_onload(doc: Document, method: str | None = None) -> None:
	"""BOM onload: whether QA has approved it, or what still stands in the way, for the form."""
	doc.set_onload(
		"bom_approval",
		{
			"approved": doc.docstatus == 1,
			"active": bool(doc.is_active),
			"missing": [] if doc.docstatus else missing_for_approval(doc),
		},
	)


def guard_work_order(doc: Document, method: str | None = None) -> None:
	"""Work Order before_validate: made only from an approved (submitted), active bill of its own
	product. At `before_validate`, which Production Plan's work orders (made without validation)
	pass through too."""
	bom = (
		frappe.db.get_value("BOM", doc.bom_no, ["docstatus", "is_active", "item"], as_dict=True)
		if doc.get("bom_no")
		else None
	)
	if not bom or bom.docstatus != 1 or not bom.is_active or bom.item != doc.production_item:
		frappe.throw(
			_(
				"A work order is made from an approved, active bill of materials of {0}; {1} is not one."
			).format(doc.production_item, doc.get("bom_no") or _("none was given"))
		)
