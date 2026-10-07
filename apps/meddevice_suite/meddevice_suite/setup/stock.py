"""Stock settings the requirement fixes on every site (module 18; D2, D3 lot control).

Only what the diagrams require whatever the client's details; everything else
stays as ERPNext ships it.
"""

import frappe

STOCK_SETTINGS = {
	# D2 "Inventory by Batch (Lot Control)" and D3's batch / lot traceability flow:
	# every lot of material and product is a batch. ERPNext v16 ships this switched off.
	"enable_serial_and_batch_no_for_item": 1,
	# D3 "Putaway in Finished Goods Warehouse (FIFO / FEFO)": the lot that expires first
	# is picked first.
	"pick_serial_and_batch_based_on": "Expiry",
	# A negative balance would break the traceability of every lot it touches.
	"allow_negative_stock": 0,
	"allow_negative_stock_for_batch": 0,
}


def apply_stock_settings() -> None:
	settings = frappe.get_single("Stock Settings")
	if all(settings.get(field) == value for field, value in STOCK_SETTINGS.items()):
		return
	settings.update(STOCK_SETTINGS)
	settings.save(ignore_permissions=True)  # setup code
