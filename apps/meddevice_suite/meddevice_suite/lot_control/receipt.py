"""Receipt of goods and incoming inspection (module 16; URS-QC-001, URS-QC-002;
D3 "Document Verification (COA / COC / ISO)", "Incoming Quality Inspection (IQC)").

On each line of a goods receipt Stores records the supplier's lot number and attaches
the supplier's certificate; the QC Inspector then verifies the certificate against the
purchase specification (`verify_certificate`). Incoming inspection of the lot, ERPNext's
Quality Inspection of type Incoming against the item's template, cannot start until the
certificate is verified (`guard_incoming_inspection`), and each numeric reading names
the instrument it was read with. A lot ordered from a conditionally approved supplier is
inspected in full (module 4, `master_data.supplier_approval`). The decision on the lot is
QA's, in `release`.

Who verifies was decided under the client's instruction of 2026-10-08: verification of
purchased product is a quality activity (ISO 13485 7.4.3), so QC verifies and Stores
attaches.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

from meddevice_suite.compliance.audit import same
from meddevice_suite.lot_control.lot_status import lots_of_row
from meddevice_suite.master_data.supplier_approval import check_full_inspection

QC_ROLES = frozenset({"QC Inspector", "Quality Manager"})
INCOMING = "Incoming"
# Set only by the QC Inspector's verification.
VERIFICATION_FIELDS = (
	"mds_certificate_verified",
	"mds_certificate_verified_by",
	"mds_certificate_verified_at",
)
# What the verification vouches for; fixed once the line is verified.
CERTIFICATE_FIELDS = ("mds_certificate", "mds_supplier_lot_no")

CUSTOM_FIELDS = {
	"Purchase Receipt Item": [
		{
			"fieldname": "mds_certificate_section",
			"label": "Supplier Certificate",
			"fieldtype": "Section Break",
			"insert_after": "batch_no",
			"collapsible": 0,
		},
		{
			"fieldname": "mds_supplier_lot_no",
			"label": "Supplier Lot No",
			"fieldtype": "Data",
			"insert_after": "mds_certificate_section",
			"allow_on_submit": 1,
			"in_list_view": 0,
		},
		{
			"fieldname": "mds_certificate",
			"label": "Supplier Certificate (COA / COC)",
			"fieldtype": "Attach",
			"insert_after": "mds_supplier_lot_no",
			"allow_on_submit": 1,
		},
		{
			"fieldname": "mds_certificate_column",
			"fieldtype": "Column Break",
			"insert_after": "mds_certificate",
		},
		{
			"fieldname": "mds_certificate_verified",
			"label": "Certificate Verified",
			"fieldtype": "Check",
			"insert_after": "mds_certificate_column",
			"allow_on_submit": 1,
			"read_only": 1,
			"no_copy": 1,
			"description": "Set by the QC Inspector after checking the certificate against the specification.",
		},
		{
			"fieldname": "mds_certificate_verified_by",
			"label": "Verified By",
			"fieldtype": "Link",
			"options": "User",
			"insert_after": "mds_certificate_verified",
			"allow_on_submit": 1,
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "mds_certificate_verified_at",
			"label": "Verified At",
			"fieldtype": "Datetime",
			"insert_after": "mds_certificate_verified_by",
			"allow_on_submit": 1,
			"read_only": 1,
			"no_copy": 1,
		},
	],
	"Quality Inspection Reading": [
		{
			"fieldname": "mds_instrument",
			"label": "Instrument",
			"fieldtype": "Link",
			"options": "Asset",
			"insert_after": "reading_value",
			"in_list_view": 1,
			"description": "The instrument the reading was taken with; required for a measured value.",
		}
	],
}


def apply_receipt_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def is_qc(user: str | None = None) -> bool:
	return bool(QC_ROLES & set(frappe.get_roles(user or frappe.session.user)))


# --- Certificate verification ----------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def verify_certificate(receipt: str, row: str) -> None:
	"""The QC Inspector records that a receipt line's supplier certificate is verified."""
	doc = frappe.get_doc("Purchase Receipt", receipt)
	doc.check_permission("read")
	if not is_qc():
		frappe.throw(_("Only QC verifies a supplier certificate."), frappe.PermissionError)
	line = next((item for item in doc.items if item.name == row), None)
	if line is None:
		frappe.throw(_("{0} has no line {1}.").format(receipt, row))
	if doc.docstatus != 1:
		frappe.throw(_("Verify the certificate once the receipt is submitted."))
	if not line.mds_certificate:
		frappe.throw(_("Line {0} has no supplier certificate attached yet.").format(line.idx))
	if line.mds_certificate_verified:
		frappe.throw(_("Line {0} is already verified.").format(line.idx))
	line.mds_certificate_verified = 1
	line.mds_certificate_verified_by = frappe.session.user
	line.mds_certificate_verified_at = now_datetime()
	doc.flags.audit_reason = _("Supplier certificate verified for line {0}").format(line.idx)
	doc.flags.certificate_verification = True
	# QC reads receipts but does not write them; this one change is the verification itself,
	# made after the checks above and recorded in the audit trail under the inspector.
	doc.save(ignore_permissions=True)


def protect_certificate(doc: Document, method: str | None = None) -> None:
	"""Purchase Receipt validate and before_update_after_submit: only the QC Inspector's
	verification sets a line verified, and a verified line's certificate and supplier lot
	number no longer change, so the verification always vouches for what is there.
	A certificate found wrong after verification is corrected by returning the lot."""
	if doc.flags.certificate_verification:
		return
	before = doc.get_doc_before_save()
	stored = {row.name: row for row in (before.items if before else [])}
	for row in doc.items:
		old = stored.get(row.name)
		for field in VERIFICATION_FIELDS:
			if not same(old.get(field) if old else None, row.get(field), row.meta.get_field(field).fieldtype):
				frappe.throw(_("Only QC verifies a supplier certificate."), frappe.PermissionError)
		if (
			old
			and cint(old.get("mds_certificate_verified"))
			and any(not same(old.get(field), row.get(field)) for field in CERTIFICATE_FIELDS)
		):
			frappe.throw(
				_("The supplier certificate of line {0} has been verified; it cannot be changed.").format(
					row.idx
				),
				frappe.PermissionError,
			)


def receipt_line_of(lot: str) -> frappe._dict | None:
	"""The goods receipt line that brought the lot in, if a supplier delivered it."""
	reference_doctype, reference_name = frappe.db.get_value(
		"Batch", lot, ["reference_doctype", "reference_name"]
	)
	if reference_doctype != "Purchase Receipt" or not reference_name:
		return None
	receipt = frappe.get_doc("Purchase Receipt", reference_name)
	for row in receipt.items:
		if lot in lots_of_row(row):
			return frappe._dict(
				receipt=receipt.name,
				row=row.name,
				idx=row.idx,
				item_code=row.item_code,
				supplier=receipt.supplier,
				certificate=row.get("mds_certificate"),
				verified=cint(row.get("mds_certificate_verified")),
				full_inspection=cint(row.get("mds_full_inspection")),
				quantity=received_quantity(row, lot),
			)
	return None


def received_quantity(row: Document, lot: str) -> float:
	"""How much of the lot the receipt line brought in, in the item's stock unit."""
	if row.get("batch_no"):
		return flt(row.stock_qty)
	return abs(
		flt(
			frappe.db.get_value(
				"Serial and Batch Entry",
				{"parent": row.serial_and_batch_bundle, "batch_no": lot},
				[{"SUM": "qty"}],
			)
		)
	)


# --- Incoming inspection ---------------------------------------------------------------------


def guard_incoming_inspection(doc: Document, method: str | None = None) -> None:
	"""Quality Inspection validate: an incoming inspection names the lot it inspects, inspects
	the line that brought it in, waits for the certificate, and measures with an instrument."""
	if doc.inspection_type != INCOMING:
		return
	if not doc.batch_no:
		frappe.throw(_("Name the lot this incoming inspection is of."))
	if doc.reference_type == "Purchase Receipt":
		line = receipt_line_of(doc.batch_no)
		if line is None or line.receipt != doc.reference_name or line.item_code != doc.item_code:
			frappe.throw(
				_("Lot {0} of {1} did not arrive on receipt {2}.").format(
					doc.batch_no, doc.item_code, doc.reference_name
				)
			)
		if not line.verified:
			frappe.throw(
				_("Verify the supplier certificate on {0} line {1} before inspecting lot {2}.").format(
					line.receipt, line.idx, doc.batch_no
				),
				title=_("Certificate not verified"),
			)
		doc.child_row_reference = line.row
		check_full_inspection(doc, line)
	# The readings come from the item's specification (module 6, master_data.specification),
	# which an inspection is refused without.
	for reading in doc.readings:
		if cint(reading.numeric) and not reading.get("mds_instrument"):
			frappe.throw(
				_("Reading {0} ({1}) is a measurement; name the instrument it was taken with.").format(
					reading.idx, reading.specification
				)
			)


def accepted_inspection(lot: str, after: str | None = None) -> str | None:
	"""The newest submitted, accepted incoming inspection of the lot, made after `after`. Made,
	not last modified: an older inspection changed after a hold is not a new inspection."""
	filters = {"batch_no": lot, "inspection_type": INCOMING, "docstatus": 1, "status": "Accepted"}
	if after:
		filters["creation"] = [">", after]
	rows = frappe.get_all(
		"Quality Inspection", filters=filters, pluck="name", order_by="creation desc", limit=1
	)
	return rows[0] if rows else None
