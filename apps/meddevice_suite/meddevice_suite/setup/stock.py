"""Stock settings the requirement fixes on every site (module 18; D2, D3 lot control).

Only what the diagrams require whatever the client's details; everything else
stays as ERPNext ships it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

STOCK_SETTINGS = {
	# D2 "Inventory by Batch (Lot Control)" and D3's batch / lot traceability flow:
	# every lot of material and product is a batch. ERPNext v16 ships this switched off.
	"enable_serial_and_batch_no_for_item": 1,
	# D3 "Putaway in Finished Goods Warehouse (FIFO / FEFO)": the lot that expires first
	# is picked first.
	"pick_serial_and_batch_based_on": "Expiry",
	# ERPNext applies the choice above only while it picks the lots itself.
	"auto_create_serial_and_batch_bundle_for_outward": 1,
	# A negative balance would break the traceability of every lot it touches.
	"allow_negative_stock": 0,
	"allow_negative_stock_for_batch": 0,
	# D3: goods are received into quarantine first and the lot is inspected afterwards (module
	# 16); ERPNext's own gate, an inspection before the receipt, stays off on every item.
	"allow_to_make_quality_inspection_after_purchase_or_delivery": 1,
}
# ERPNext also lets single items and lots go negative, whatever the settings say.
NEGATIVE_STOCK_SWITCH = {"Item": "allow_negative_stock", "Batch": "allow_negative_stock_for_batch"}


def apply_stock_settings() -> None:
	settings = frappe.get_single("Stock Settings")
	if all(settings.get(field) == value for field, value in STOCK_SETTINGS.items()):
		return
	settings.update(STOCK_SETTINGS)
	settings.flags.declared_by_code = True
	settings.save(ignore_permissions=True)  # setup code


def protect_stock_settings(doc: Document, method: str | None = None) -> None:
	"""Stock Settings validate: the declared settings change only by migrate."""
	if doc.flags.declared_by_code or frappe.flags.in_install or frappe.flags.in_migrate:
		return
	changed = [field for field, value in STOCK_SETTINGS.items() if doc.get(field) != value]
	if changed:
		frappe.throw(
			_("{0} are part of the validated configuration and cannot be changed here.").format(
				", ".join(_(doc.meta.get_label(field)) for field in changed)
			),
			frappe.PermissionError,
		)


def refuse_negative_stock(doc: Document, method: str | None = None) -> None:
	"""Item and Batch validate: no item or lot may be allowed to go below zero (URS-STK-003)."""
	field = NEGATIVE_STOCK_SWITCH[doc.doctype]
	if doc.get(field):
		frappe.throw(
			_("Stock can never go below zero, for any item or lot; {0} cannot be switched on.").format(
				_(doc.meta.get_label(field))
			)
		)
