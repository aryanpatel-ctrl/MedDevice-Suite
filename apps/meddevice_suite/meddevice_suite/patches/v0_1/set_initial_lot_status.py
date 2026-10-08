"""Give every existing lot its status when lot control is installed (URS-STK-007).

A lot with stock in a Rejected, Hold or Quarantine store takes that status, in that
order of caution; every other lot becomes Approved. Each change is an audit event
with the reason below. Lots that already have a status are left alone, so the patch
can run again without effect. The fields are created here as well, because patches
run before the after-migrate configuration.
"""

import frappe

from meddevice_suite.lot_control.lot_status import (
	STATUS_FIELD,
	apply_lot_status,
	initial_status,
	set_status,
	stock_by_lot,
)
from meddevice_suite.lot_control.stores import apply_store_roles

REASON = "Migration: initial lot status from the store holding the lot"


def execute() -> None:
	apply_store_roles()
	apply_lot_status()
	holdings: dict[str, list] = {}
	for row in stock_by_lot():
		holdings.setdefault(row.batch_no, []).append(row)
	pending = frappe.get_all(
		"Batch", filters={"mds_status_set_at": ["is", "not set"]}, fields=["name", STATUS_FIELD], as_list=True
	)
	for lot, _status in pending:
		set_status(lot, initial_status(holdings.get(lot, [])), REASON)
