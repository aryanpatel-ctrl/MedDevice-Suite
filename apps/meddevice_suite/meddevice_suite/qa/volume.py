"""Volume harness (URS-PLT-021): years of data on a test site, and how the hot paths fare on it.

	bench --site <test-site> execute meddevice_suite.qa.volume.run --kwargs "{'scale': 1}"

Run locally, not in CI: at scale 1 it writes for several minutes. It refuses to run on any site that is
not a test site (`"allow_tests": 1` in the site's own configuration, as `qa.site_setup` requires), so
it can never touch a working or production site. `generate(scale)` adds the data, `measure()` times each
hot path on it against its budget and prints PASS or FAIL, and `check_indexes()` lists the indexes the
hot paths rely on and whether the site has them. `run(scale, generate_data=False)` measures again
without adding more.

How the data is made (scale 1, about): 1,000 items, 2,000 lots, 20,000 stock ledger entries, 50,000
audit events, 2,000 signatures, 1,000 reminders, 500 training records and 300 controlled documents.

- Audit events and electronic signatures are made through their own services (`audit.record`,
  `esign.sign` with a harness signer's password): each is fingerprinted with the one before it on
  its record, so a row written any other way would break the chain that `verify_events`,
  `verify_signatures` and the anchors check, and would measure a trail the site can never have. They
  carry the time they were written, as the services set it; what the hot paths feel is how many there
  are and how long each record's chain is, which follows the real distribution (most records a few,
  some thousands).
- Reminders and training records are made through their services too (`reminders.make` and
  `complete`, `training.reconcile` from employees and approved matrices), which are the only writers
  of those records and keep their keys and rules.
- Items, lots, controlled documents with their revisions, and the stock ledger are history, written
  with bulk inserts as a test fixture writes them (as setup/test_stock.py does with `db.set_value`):
  they carry no chained fingerprint, and the rules their forms enforce were met by the history they
  stand for. The ledger is consistent with itself: each store's running quantity, value and FIFO
  queue follow from the entries before it, in posting order, its bins hold the final balance, and
  every entry is dated before today, so a movement posted now follows it as ERPNext expects. Their
  creation is not in the audit trail; on a test site that is what a fixture means.
- The movements measured are real: a lot received and issued now through ERPNext and every guard of
  this app, among the hundreds of lots of its item in the history. `measure` rolls back what it
  wrote, so it can be run again on the same data.
"""

import json
import random
import statistics
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import click
import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime, today

from meddevice_suite.setup.site_flags import site_flag

# --- What is made, at scale 1 ---------------------------------------------------------------------

AT_SCALE_1 = {
	"items": 1000,
	"lots": 2000,
	"ledger_entries": 20000,
	"audit_events": 50000,
	"signatures": 2000,
	"reminders": 1000,
	"training_records": 500,
	"documents": 300,
}
YEARS = 3  # how far back the history goes
HOT_ITEMS = 3  # raw materials received every few days: hundreds of lots each, as a catheter's silicone
HOT_SHARE = 0.45  # the share of the lots that are theirs
SHELF_LIFE_DAYS = 730
RATE = 10  # the valuation of every unit, so the ledger's values follow from its quantities
LOT_QTY = 90
STILL_IN_STOCK_DAYS = 120  # lots made within this many days still have stock
JOBS, DOCUMENTS_PER_JOB = 5, 10
PREFIX = "VOL"
SIGNER = "volume.signer@example.com"
COMMIT_EVERY = 500
SAVEPOINT = "volume_path"

# --- Budgets: the most each hot path may take on the data, in milliseconds ------------------------

BUDGET_MS = {
	"stock movement: guards on its ledger entries": 150,
	"stock movement: whole issue through ERPNext": 3000,
	"stock count: lots counted back (100 lots)": 50,
	"lot dates: stock history of a lot": 20,
	"audit trail: write an event (longest chain)": 50,
	"audit trail: verify a record (longest chain)": 1000,
	"audit trail: hourly anchor": 1000,
	"audit trail: check the anchors": 1000,
	"signature: sign (longest chain, with password check)": 1000,
	"signature: verify a record (longest chain)": 200,
	"My Work": 1000,
	"reminders: due items of a category": 500,
	"reminders: daily run": 20000,
	"alerts: daily supplier, licence, change and review alerts": 2000,
	"training: daily reconciliation": 3000,
	"training: operators of a job card (50)": 200,
	"master data: items of a 100-line record": 100,
	"master data: operations and workstations (50)": 100,
	"report: Stock by Lot Status": 3000,
	"report: Lots Awaiting Release": 3000,
	"report: Licence Register": 500,
}

# --- Indexes the hot paths rely on ----------------------------------------------------------------

# (record type, leading columns of an index): the app's own tables, and the indexes it configures on
# ERPNext's (setup/indexes.py). The test test_query_budgets.TestIndexes holds the site to these.
APP_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
	("Audit Event", ("reference_doctype", "reference_name")),  # chain head, verify, timeline
	("Audit Event", ("previous_event",)),  # chain head: the event no other follows
	("Audit Event", ("user",)),  # a user's history; deleting a user
	("Audit Event", ("event_at",)),  # date ranges, archive
	("Audit Event", ("creation",)),  # anchors: newest entry, counts
	("Electronic Signature", ("reference_doctype", "reference_name")),
	("Electronic Signature", ("previous_signature",)),
	("Electronic Signature", ("signer",)),
	("Electronic Signature", ("creation",)),
	("Audit Anchor", ("ledger", "creation")),
	("Reminder", ("category",)),  # the daily run, per category
	("Reminder", ("status",)),  # My Work, follow-ups, notices
	("Reminder", ("reminder_key",)),
	("Work Task", ("status",)),
	("Work Task", ("assigned_to",)),
	("Work Task", ("approver",)),
	("Training Record", ("employee",)),  # reconciliation, competence at the job card
	("Training Record", ("document_revision",)),
	("Training Record", ("status",)),
	("Document Revision", ("controlled_document",)),  # revisions to train on (added for URS-PLT-021)
	("Document Revision", ("status",)),  # My Work, revisions taking effect (added for URS-PLT-021)
	("Lot Release", ("batch",)),
	("Supplier Approval", ("supplier",)),
	("Customer Licence", ("customer",)),
	("Batch", ("mds_lot_status",)),  # this app's column on ERPNext's lot: reports, quarantine list
	("Batch", ("expiry_date",)),  # expiry alerts and reminders
	# Configured by this app on ERPNext's tables (setup/indexes.py).
	("Quality Inspection", ("batch_no",)),  # a lot's newest inspection: release, Lots Awaiting Release
	("Serial and Batch Bundle", ("voucher_no",)),  # what a document takes of each lot (FEFO)
	# The file columns an evidence file is looked up by (compliance/evidence_files.py).
	("Document Revision", ("file",)),
	("Record Archive", ("archive_file",)),
	("Purchase Receipt Item", ("mds_certificate",)),
	("Supplier Approval", ("certificate",)),
	("Customer Licence", ("licence_copy",)),
	("Important File Version", ("file",)),
	("Calibration Record", ("certificate",)),
	("Training Record", ("certificate",)),
)
# ERPNext's tables, checked and reported, never changed (rules.md section 1).
ERPNEXT_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
	("Stock Ledger Entry", ("batch_no",)),  # origins of a lot, its stock history, last arrival
	("Stock Ledger Entry", ("voucher_no", "voucher_type")),  # a count's own entries
	("Stock Ledger Entry", ("item_code", "warehouse")),  # ERPNext's lot quantities (FEFO)
	("Stock Ledger Entry", ("serial_and_batch_bundle",)),
	("Serial and Batch Entry", ("parent",)),  # a bundle's lots
	("Serial and Batch Entry", ("batch_no",)),  # origins of a lot, its stock history
	("Bin", ("item_code", "warehouse")),
	("Stock Entry Detail", ("parent",)),
	("Delivery Note Item", ("parent",)),
	("Packed Item", ("parent",)),
	("Purchase Receipt Item", ("parent",)),
	("Employee", ("status",)),
	("Employee", ("designation",)),
)


def refuse_unless_test_site() -> None:
	"""Only a site marked as a test site in its own configuration: the harness writes thousands of
	records and can never be allowed near a working or production site."""
	if not site_flag("allow_tests"):
		frappe.throw(_("{0} is not a test site; refusing to load volume data").format(frappe.local.site))


def run(scale: float = 1, generate_data: bool = True) -> None:
	refuse_unless_test_site()
	if generate_data:
		generate(scale)
	measure()
	check_indexes()


def echo(text: str) -> None:
	# The harness is run from the command line by a person, who reads its progress and its table.
	click.echo(text)


# --- Counting queries -----------------------------------------------------------------------------


@contextmanager
def counted() -> Iterator[list[str]]:
	"""The SQL statements run inside the block, counted the way Frappe's
	`IntegrationTestCase.assertQueryCount` counts them: every call of the database's `sql`, which the
	query builder and `get_all` go through."""
	queries: list[str] = []
	original = frappe.db.__class__.sql

	def sql(db, *args, **kwargs):
		result = original(db, *args, **kwargs)
		queries.append(str(db.last_query))
		return result

	frappe.db.__class__.sql = sql
	try:
		yield queries
	finally:
		frappe.db.__class__.sql = original


# --- Indexes --------------------------------------------------------------------------------------


def index_present(doctype: str, columns: tuple[str, ...] | list[str]) -> bool:
	"""Whether some index of the table starts with `columns`, in that order (the primary key counts)."""
	if not frappe.db.table_exists(doctype):
		return False
	indexes: dict[str, list[tuple[int, str]]] = {}
	for row in frappe.db.sql(
		"""select index_name as `index`, seq_in_index as seq, column_name as `column`
		from information_schema.statistics where table_schema = database() and table_name = %s""",
		f"tab{doctype}",
		as_dict=True,
	):
		indexes.setdefault(row["index"], []).append((int(row.seq), row.column))
	wanted = list(columns)
	return any(
		[column for _seq, column in sorted(parts)][: len(wanted)] == wanted for parts in indexes.values()
	)


def check_indexes() -> list[tuple[str, str, str, bool]]:
	"""Each index the hot paths rely on: the app's own tables must have them all; ERPNext's are
	reported as they are."""
	rows = [
		(owner, doctype, ", ".join(columns), index_present(doctype, columns))
		for owner, indexes in (("app", APP_INDEXES), ("ERPNext", ERPNEXT_INDEXES))
		for doctype, columns in indexes
	]
	echo("\nIndexes the hot paths rely on")
	echo(f"{'owner':8} {'table':26} {'columns':40} present")
	for owner, doctype, columns, present in rows:
		mark = "yes" if present else ("MISSING" if owner == "app" else "no (ERPNext, not changed)")
		echo(f"{owner:8} {doctype:26} {columns:40} {mark}")
	return rows


# --- Generation -----------------------------------------------------------------------------------


def scaled(kind: str, scale: float) -> int:
	return max(1, round(AT_SCALE_1[kind] * scale))


def generate(scale: float = 1) -> dict:
	"""Years of data on the test site, committed in chunks; returns what was made."""
	refuse_unless_test_site()
	from meddevice_suite.qa.site_setup import approve_test_stores, ensure_fiscal_years

	frappe.set_user("Administrator")  # nosemgrep: frappe-setuser - test site only
	ensure_fiscal_years()
	approve_test_stores()  # the stores QA has approved on a site in use
	random.seed(20261015)  # the same history for the same scale, so runs compare
	tag = frappe.generate_hash(length=4).upper()
	company = frappe.defaults.get_global_default("company")
	started = time.monotonic()

	hot = hot_items(tag, company)
	items = hot + bulk_items(tag, scaled("items", scale) - len(hot))
	documents = controlled_documents(tag, scaled("documents", scale))
	lots = history_of_lots(tag, company, items, hot, scaled("lots", scale), scaled("ledger_entries", scale))
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site, in chunks
	echo(f"items {len(items)}, lots {len(lots)}, documents {len(documents)} ({elapsed(started)})")

	training_records(tag, company, documents, scaled("training_records", scale))
	echo(f"training records ({elapsed(started)})")
	made_reminders(lots, scaled("reminders", scale))
	echo(f"reminders ({elapsed(started)})")
	signatures(lots, scaled("signatures", scale))
	echo(f"signatures ({elapsed(started)})")
	audit_events(lots, items, documents, scaled("audit_events", scale))
	echo(f"audit events ({elapsed(started)})")

	from meddevice_suite.compliance.audit_anchor import anchor_trail

	anchor_trail()  # the hourly anchor, as the site's scheduler would have made it
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site
	made = {
		"items": len(items),
		"lots": len(lots),
		"ledger_entries": frappe.db.count(
			"Stock Ledger Entry", {"voucher_no": ["like", f"{PREFIX}-{tag}-%"]}
		),
		"audit_events": frappe.db.count("Audit Event"),
		"signatures": frappe.db.count("Electronic Signature"),
		"reminders": frappe.db.count("Reminder"),
		"training_records": frappe.db.count("Training Record"),
		"documents": len(documents),
	}
	echo(f"generated in {elapsed(started)}: {json.dumps(made)}")
	return made


def elapsed(started: float) -> str:
	return f"{time.monotonic() - started:.0f} s"


def hot_items(tag: str, company: str) -> list[str]:
	"""Real, approved raw materials, made through the Item form as any item is: the measured
	movements are posted on them through ERPNext."""
	from meddevice_suite.master_data import item_master

	stores_name = approved_store(company)
	codes = []
	for i in range(HOT_ITEMS):
		code = f"{PREFIX}-HOT-{tag}-{i + 1}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": f"Silicone compound {i + 1} (volume)",
				"item_group": "Raw Material",
				"stock_uom": "Nos",
				"gst_hsn_code": "39269099",
				"is_stock_item": 1,
				"has_batch_no": 1,
				"create_new_batch": 1,
				"batch_number_series": f"{code}-.#####",
				"has_expiry_date": 1,
				"shelf_life_in_days": SHELF_LIFE_DAYS,
				"valuation_rate": RATE,
				item_master.ITEM_TYPE: item_master.RAW_MATERIAL,
				item_master.STORAGE_CONDITION: "Below 30 °C (volume)",
				"item_defaults": [{"company": company, "default_warehouse": stores_name}],
			}
		).insert()
		approved_as_fixture(
			"Item", code, item_master.APPROVAL_STATUS, item_master.APPROVED_BY, item_master.APPROVED_AT
		)
		codes.append(code)
	return codes


def approved_as_fixture(doctype: str, name: str, status_field: str, by_field: str, at_field: str) -> None:
	"""QA's approval recorded as the signed act leaves it (a test-site fixture, as setup/test_stock.py
	records it); the act itself is tested with its signature in master_data."""
	frappe.db.set_value(
		doctype, name, {status_field: "Approved", by_field: "Administrator", at_field: now_datetime()}
	)


def approved_store(company: str) -> str:
	return frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": "Stores", "is_group": 0})


def columns_of(doctype: str, row: dict) -> dict:
	"""The row with only the columns the site's table has (custom fields arrive with the configuration)."""
	meta = frappe.get_meta(doctype)
	standard = {"name", "creation", "modified", "owner", "modified_by", "docstatus", "idx"}
	return {key: value for key, value in row.items() if key in standard or meta.has_field(key)}


def bulk(doctype: str, rows: list[dict]) -> None:
	if not rows:
		return
	rows = [columns_of(doctype, row) for row in rows]
	fields = sorted({key for row in rows for key in row})
	frappe.db.bulk_insert(doctype, fields, [tuple(row.get(field) for field in fields) for row in rows])


def stamp(when: datetime) -> dict:
	return {"creation": when, "modified": when, "owner": "Administrator", "modified_by": "Administrator"}


def bulk_items(tag: str, count: int) -> list[str]:
	"""Items of every type, most approved, a tenth still in draft (waiting for QA in My Work)."""
	from meddevice_suite.master_data import item_master

	types = item_master.ITEM_TYPES
	rows, codes = [], []
	for i in range(count):
		code = f"{PREFIX}-ITEM-{tag}-{i + 1:05d}"
		kind = types[i % len(types)]
		lot_tracked = kind in item_master.LOT_TRACKED_TYPES
		draft = i % 10 == 9
		when = add_to_date(now_datetime(), days=-random.randint(1, 365 * YEARS))
		rows.append(
			{
				"name": code,
				"item_code": code,
				"item_name": f"{kind} {i + 1} (volume)",
				"item_group": "Raw Material",
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"has_batch_no": int(lot_tracked),
				"has_expiry_date": int(lot_tracked),
				"shelf_life_in_days": SHELF_LIFE_DAYS if lot_tracked else 0,
				"valuation_rate": RATE,
				"disabled": 0,
				"gst_hsn_code": "39269099",
				item_master.ITEM_TYPE: kind,
				item_master.STORAGE_CONDITION: "Below 30 °C (volume)",
				item_master.APPROVAL_STATUS: item_master.DRAFT if draft else item_master.APPROVED,
				item_master.APPROVED_BY: None if draft else "Administrator",
				item_master.APPROVED_AT: None if draft else when,
				**stamp(when),
			}
		)
		codes.append(code)
	bulk("Item", rows)
	return codes


def controlled_documents(tag: str, count: int) -> list[str]:
	"""Effective documents, each with its revisions (the latest effective, the earlier superseded);
	a few have their periodic review falling due."""
	documents, revisions = [], []
	names = []
	for i in range(count):
		name = f"{PREFIX}-DOC-{tag}-{i + 1:04d}"
		made = add_to_date(now_datetime(), days=-random.randint(400, 365 * YEARS))
		count_revisions = 1 + i % 3
		revision_names = [f"{name}-R{r + 1:02d}" for r in range(count_revisions)]
		for r, revision in enumerate(revision_names):
			current = r == count_revisions - 1
			revisions.append(
				{
					"name": revision,
					"controlled_document": name,
					"document_title": f"Procedure {i + 1} (volume)",
					"document_type": "SOP",
					"revision": f"{r + 1:02d}",
					"status": "Effective" if current else "Superseded",
					"superseded_by": None if current else revision_names[r + 1],
					"author": "Administrator",
					"reviewer": "Administrator",
					"approver": "Administrator",
					"effective_date": getdate(add_days(made, 30 * (r + 1))),
					"docstatus": 1,
					**stamp(add_to_date(made, days=30 * r)),
				}
			)
		review_in = random.randint(-10, 700)
		documents.append(
			{
				"name": name,
				"title": f"Procedure {i + 1} (volume)",
				"document_type": "SOP",
				"department": "QA",
				"process_owner": "Administrator",
				"review_period_years": 2,
				"status": "Effective",
				"current_revision": revision_names[-1],
				"effective_since": getdate(add_days(made, 30 * count_revisions)),
				"next_review_date": add_days(today(), review_in),
				**stamp(made),
			}
		)
		names.append(name)
	bulk("Controlled Document", documents)
	bulk("Document Revision", revisions)
	return names


def history_of_lots(
	tag: str, company: str, items: list[str], hot: list[str], count: int, ledger_entries: int
) -> list[str]:
	"""Lots made over the years and their stock ledger: received into quarantine, released into the
	approved store and issued from it until used up; the recent ones still in stock, some still in
	quarantine; a few rejected or on hold, kept in their stores."""
	from meddevice_suite.lot_control import stores
	from meddevice_suite.lot_control.lot_status import APPROVED, HOLD, QUARANTINE, REJECTED, STATUS_FIELD

	store_of = {
		APPROVED: approved_store(company),
		QUARANTINE: stores.store_for(stores.QUARANTINE, company),
		HOLD: stores.store_for(stores.HOLD, company),
		REJECTED: stores.store_for(stores.REJECTED, company),
	}
	lot_tracked = set(
		frappe.get_all("Item", filters={"name": ["in", items], "has_batch_no": 1}, pluck="name")
	)
	others = [item for item in items if item in lot_tracked and item not in hot] or hot
	issues_per_lot = max(1, ledger_entries // count - 3)  # receipt, release (two entries), issues
	start = now_datetime() - timedelta(days=365 * YEARS)
	entries: list[dict] = []
	lots: list[dict] = []
	for i in range(count):
		item = hot[i % len(hot)] if random.random() < HOT_SHARE else random.choice(others)
		made = start + timedelta(days=(365 * YEARS - 2) * i / count, hours=random.randint(8, 16))
		lot = f"{PREFIX}-LOT-{tag}-{i + 1:05d}"
		age = (now_datetime() - made).days
		roll = random.random()
		# Recent lots are still in quarantine; of the older, a few were rejected or put on hold.
		status = QUARANTINE if age < 20 else REJECTED if roll < 0.03 else HOLD if roll < 0.04 else APPROVED
		voucher = f"{PREFIX}-{tag}-{i + 1:05d}"

		def entry(
			when: datetime, warehouse: str, qty: float, n: int, *, lot=lot, item=item, voucher=voucher
		) -> None:
			entries.append(
				{
					"name": f"{voucher}-{n:02d}",
					"item_code": item,
					"warehouse": warehouse,
					"batch_no": lot,
					"voucher_type": "Stock Entry",
					"voucher_no": voucher,
					"voucher_detail_no": f"{voucher}-L{n:02d}",
					"actual_qty": qty,
					"posting_datetime": when,
					"company": company,
				}
			)

		entry(made, store_of[QUARANTINE], LOT_QTY, 0)
		left = LOT_QTY
		if status != QUARANTINE:
			released = made + timedelta(days=5)
			kept_in = store_of[status]
			entry(released, store_of[QUARANTINE], -LOT_QTY, 1)
			entry(released, kept_in, LOT_QTY, 2)
			if status == APPROVED:
				still_in_stock = age < STILL_IN_STOCK_DAYS
				issues = issues_per_lot // 2 if still_in_stock else issues_per_lot
				each = LOT_QTY / (issues_per_lot + (1 if still_in_stock else 0))
				for n in range(issues):
					when = released + timedelta(days=1 + n * max(1, min(age - 6, 200)) / max(issues, 1))
					if when >= now_datetime() - timedelta(days=1):
						break
					qty = each if n < issues - 1 or still_in_stock else left
					entry(when, kept_in, -qty, 3 + n)
					left -= qty
		lots.append(
			{
				"name": lot,
				"batch_id": lot,
				"item": item,
				"stock_uom": "Nos",
				"manufacturing_date": made.date(),
				"expiry_date": (made + timedelta(days=SHELF_LIFE_DAYS)).date(),
				"batch_qty": round(left, 6),
				"use_batchwise_valuation": 1,
				"disabled": 0,
				STATUS_FIELD: status,
				"mds_status_set_by": None if status == QUARANTINE else "Administrator",
				"mds_status_set_at": None if status == QUARANTINE else made + timedelta(days=5),
				"mds_status_reason": None if status == QUARANTINE else "Decided in the volume history",
				**stamp(made),
			}
		)
	bulk("Batch", lots)
	bulk("Stock Ledger Entry", ledger_in_order(entries))
	return [lot["name"] for lot in lots]


def ledger_in_order(entries: list[dict]) -> list[dict]:
	"""The entries in posting order, each with the running quantity, value and FIFO queue of its item
	in its store, as ERPNext would have posted them; and the bins brought to the final balances."""
	running: dict[tuple[str, str], float] = {}
	rows = []
	for entry in sorted(entries, key=lambda e: (e["posting_datetime"], e["name"])):
		key = (entry["item_code"], entry["warehouse"])
		balance = round(running.get(key, 0) + entry["actual_qty"], 6)
		running[key] = balance
		when = get_datetime(entry["posting_datetime"])
		rows.append(
			{
				**entry,
				"posting_date": when.date(),
				"posting_time": when.time(),
				"has_batch_no": 1,
				"stock_uom": "Nos",
				"incoming_rate": RATE if entry["actual_qty"] > 0 else 0,
				"valuation_rate": RATE,
				"qty_after_transaction": balance,
				"stock_value": balance * RATE,
				"stock_value_difference": entry["actual_qty"] * RATE,
				"stock_queue": json.dumps([[balance, RATE]] if balance > 0 else []),
				"is_cancelled": 0,
				"docstatus": 1,
				**stamp(when),
			}
		)
	bins_to(running)
	return rows


def bins_to(balances: dict[tuple[str, str], float]) -> None:
	"""Each item's bin in each store holds its final balance: added to a bin that exists, made where
	none does."""
	existing = {
		(row.item_code, row.warehouse): row
		for row in frappe.get_all(
			"Bin",
			filters={"item_code": ["in", sorted({item for item, _store in balances}) or [""]]},
			fields=["name", "item_code", "warehouse", "actual_qty", "projected_qty"],
		)
	}
	new = []
	for (item, warehouse), qty in balances.items():
		if current := existing.get((item, warehouse)):
			name = current.name
			frappe.db.set_value(  # bulk history, as a fixture writes it
				"Bin",
				name,
				{
					"actual_qty": current.actual_qty + qty,
					"projected_qty": current.projected_qty + qty,
					"stock_value": (current.actual_qty + qty) * RATE,
					"valuation_rate": RATE,
				},
				update_modified=False,
			)
		else:
			new.append(
				{
					"name": frappe.generate_hash(length=10),
					"item_code": item,
					"warehouse": warehouse,
					"actual_qty": qty,
					"projected_qty": qty,
					"stock_uom": "Nos",
					"valuation_rate": RATE,
					"stock_value": qty * RATE,
					**stamp(now_datetime()),
				}
			)
	bulk("Bin", new)


def training_records(tag: str, company: str, documents: list[str], count: int) -> None:
	"""Jobs with an approved training matrix each, and their employees: the training each needs is
	opened by `training.reconcile`, its only writer."""
	from meddevice_suite.qms import training

	per_job = max(1, count // (JOBS * DOCUMENTS_PER_JOB))
	for j in range(JOBS):
		job = f"{PREFIX} Job {tag} {j + 1}"
		frappe.get_doc({"doctype": "Designation", "designation_name": job}).insert(ignore_permissions=True)
		matrix = frappe.get_doc(
			{
				"doctype": "Training Matrix",
				"designation": job,
				"items": [
					{
						"controlled_document": documents[(j * DOCUMENTS_PER_JOB + k) % len(documents)],
						"validity_months": 24,
					}
					for k in range(min(DOCUMENTS_PER_JOB, len(documents)))
				],
				"approved_by": "Administrator",
				"approved_at": now_datetime(),
			}
		)
		matrix.insert(ignore_permissions=True)
		# QA's signed approval, recorded as the act leaves it (tested with its signature in qms).
		matrix.flags.training = True
		matrix.flags.ignore_permissions = True
		matrix.submit()
		for e in range(per_job):
			frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": f"Operator {tag} {j + 1}-{e + 1}",
					"gender": "Female",
					"date_of_birth": "1990-01-01",
					"date_of_joining": add_days(today(), -random.randint(30, 365 * YEARS)),
					"company": company,
					"status": "Active",
					"designation": job,
				}
			).insert(ignore_permissions=True)
	training.reconcile(None)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site


def made_reminders(lots: list[str], count: int) -> None:
	"""Reminders through their service: raised for lots, most of them completed over the years, the
	rest open."""
	from meddevice_suite.work_management import reminders

	for i in range(count):
		lot = lots[i % len(lots)]
		due = add_days(today(), random.randint(-365 * YEARS, 30))
		reminder = reminders.make(
			"Lot Expiry",
			reminders.Due("Batch", lot, due, f"Lot {lot} expires (volume)", ("Stock User",), part=str(i)),
		)
		if i % 100 < 85:
			reminders.complete(reminder.name, "Done in the volume history")
		if i % COMMIT_EVERY == COMMIT_EVERY - 1:
			frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site


def harness_signer() -> str:
	"""A QA user of the harness with a password made for this run, held only in memory."""
	from frappe.auth import get_login_attempt_tracker
	from frappe.utils.password import update_password

	if not frappe.db.exists("User", SIGNER):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": SIGNER,
				"first_name": "Volume",
				"last_name": "Signer",
				"send_welcome_email": 0,
				"roles": [{"role": "Quality Manager"}],
			}
		).insert(ignore_permissions=True)
	frappe.local.volume_password = frappe.generate_hash(length=24) + "aA1#"
	update_password(SIGNER, frappe.local.volume_password)
	get_login_attempt_tracker(SIGNER, raise_locked_exception=False).add_success_attempt()
	return SIGNER


def signatures(lots: list[str], count: int) -> None:
	"""Signatures through the signing service, password checked each time: most lots one, a few
	records a long chain."""
	from meddevice_suite.compliance.esign import sign

	signer = harness_signer()
	long_chain = max(1, count // 20)
	targets = [("Batch", lots[0])] * long_chain + [("Batch", lot) for lot in lots[1:]]
	frappe.set_user(signer)  # nosemgrep: frappe-setuser - test site only
	try:
		for i, (doctype, name) in enumerate(targets[:count]):
			sign(
				frappe.get_doc(doctype, name),
				meaning="Verified",
				action="Volume harness check",
				password=frappe.local.volume_password,
				reason="Checked in the volume history",
				permission="read",
			)
			if i % COMMIT_EVERY == COMMIT_EVERY - 1:
				frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site
	finally:
		frappe.set_user("Administrator")  # nosemgrep: frappe-setuser - test site only
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site


def audit_events(lots: list[str], items: list[str], documents: list[str], target: int) -> None:
	"""Audit events through the audit trail until the trail holds `target`: changes to the lots,
	items and documents of the history, one record with a long history (a tenth of them)."""
	from meddevice_suite.compliance import audit

	references = (
		[("Batch", lot) for lot in lots]
		+ [("Item", item) for item in items]
		+ [("Controlled Document", document) for document in documents]
	)
	busiest = references[0]
	shortfall = target - frappe.db.count("Audit Event")
	for i in range(max(0, shortfall)):
		doctype, name = busiest if i % 10 == 0 else references[(i * 7919) % len(references)]
		audit.record(
			doctype, name, "Updated", {"changed": [["description", f"before {i}", f"after {i}"]]}, None
		)
		if i % COMMIT_EVERY == COMMIT_EVERY - 1:
			frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - bulk generation on a test site


# --- Measurement ----------------------------------------------------------------------------------


def timed(call: Callable[[], object], repeats: int = 3) -> tuple[float, int]:
	"""The median time of `call` in milliseconds over `repeats` runs, and the queries of the last."""
	times, queries = [], 0
	for _run in range(repeats):
		with counted() as issued:
			began = time.perf_counter()
			call()
			times.append((time.perf_counter() - began) * 1000)
		queries = len(issued)
	return statistics.median(times), queries


def measure() -> list[dict]:
	"""Each hot path timed on the site's data against its budget; a table of path, time, queries and
	PASS or FAIL. What the measured paths write is rolled back, so it can be run again."""
	refuse_unless_test_site()
	frappe.set_user("Administrator")  # nosemgrep: frappe-setuser - test site only
	results = []
	try:
		for label, budget, call, repeats in hot_paths():
			# A path that fails leaves nothing half-written for the paths after it.
			frappe.db.savepoint(SAVEPOINT)
			try:
				ms, queries = timed(call, repeats)
				results.append(
					{
						"path": label,
						"budget": BUDGET_MS[budget],
						"ms": ms,
						"queries": queries,
						"ok": ms <= BUDGET_MS[budget],
					}
				)
			except Exception as error:
				frappe.db.rollback(save_point=SAVEPOINT)
				frappe.log_error(title=f"Volume harness: {label} failed")
				results.append(
					{
						"path": label,
						"budget": BUDGET_MS[budget],
						"ms": None,
						"queries": None,
						"ok": False,
						"error": str(error),
					}
				)
	finally:
		frappe.set_user("Administrator")  # nosemgrep: frappe-setuser - test site only
		frappe.db.rollback()
	echo(f"\n{'hot path':62} {'time ms':>9} {'budget':>7} {'queries':>8}  result")
	for row in results:
		time_text = f"{row['ms']:.1f}" if row["ms"] is not None else "-"
		queries = row["queries"] if row["queries"] is not None else "-"
		result = "PASS" if row["ok"] else (f"ERROR {row['error'][:60]}" if "error" in row else "FAIL")
		echo(f"{row['path']:62} {time_text:>9} {row['budget']:>7} {queries!s:>8}  {result}")
	failed = [row["path"] for row in results if not row["ok"]]
	summary = f"\n{len(results) - len(failed)} of {len(results)} hot paths within budget"
	echo(summary + (f"; not within it: {', '.join(failed)}" if failed else ""))
	return results


Path = tuple[str, str, Callable[[], object], int]


def failing(error: Exception) -> Callable[[], object]:
	"""A path whose setting up failed: it is reported as an error in the table, not skipped."""

	def call() -> None:
		raise error

	return call


def settled(job: Callable[[], object]) -> None:
	"""A daily job run once before it is timed: what is new since its last run (alerts to send,
	reminders to raise) is done first, so the time measured is an ordinary day's."""
	try:
		job()
	except Exception:
		frappe.log_error(title="Volume harness: a daily job failed before it was timed")


def hot_paths() -> Iterator[Path]:
	"""(label, budget, call, how often to time it), each set up on the site's data as it is used."""
	yield from stock_paths()
	yield from trail_paths()
	yield from page_and_job_paths()


def stock_paths() -> Iterator[Path]:
	from meddevice_suite.lot_control import expiry, finished_goods, lot_dates, lot_status
	from meddevice_suite.master_data import warehouse

	company = frappe.defaults.get_global_default("company")
	hot = frappe.get_all(
		"Item", filters={"name": ["like", f"{PREFIX}-HOT-%"]}, pluck="name", order_by="creation desc", limit=1
	)
	if not hot:
		return
	try:
		coming_in, going_out, lot = real_movement(hot[0], company)
	except Exception as error:
		frappe.log_error(title="Volume harness: the measured movement could not be posted")
		yield (
			"stock movement: guards on its ledger entries",
			"stock movement: guards on its ledger entries",
			failing(error),
			1,
		)
		return

	def guards() -> None:
		for entry in (coming_in, going_out):
			warehouse.guard_stock_ledger_entry(entry)
			finished_goods.guard_stock_ledger_entry(entry)
			lot_status.guard_stock_ledger_entry(entry)
			expiry.guard_stock_ledger_entry(entry)

	yield (
		"stock movement: guards on its ledger entries",
		"stock movement: guards on its ledger entries",
		guards,
		5,
	)
	store = approved_store(company)
	yield (
		"stock movement: whole issue through ERPNext",
		"stock movement: whole issue through ERPNext",
		lambda: issue(hot[0], lot, store),
		1,
	)
	history = frappe.get_all("Batch", filters={"item": hot[0]}, pluck="name", limit=100)
	count = frappe._dict(
		voucher_type="Stock Reconciliation",
		voucher_no=f"{PREFIX}-NO-COUNT",
		voucher_detail_no=f"{PREFIX}-NO-LINE",
		batch_no=None,
		serial_and_batch_bundle=f"{PREFIX}-NO-BUNDLE",
		actual_qty=1,
	)
	yield (
		"stock count: lots counted back (100 lots)",
		"stock count: lots counted back (100 lots)",
		lambda: lot_status.counted_back(count, history),
		5,
	)
	yield (
		"lot dates: stock history of a lot",
		"lot dates: stock history of a lot",
		lambda: lot_dates.has_stock_history(lot),
		5,
	)


def trail_paths() -> Iterator[Path]:
	from meddevice_suite.compliance import audit, audit_anchor
	from meddevice_suite.compliance.esign import sign, verify_signatures

	audited = longest_chain("Audit Event")
	if audited:
		label = "audit trail: write an event (longest chain)"
		yield (
			label,
			label,
			lambda: audit.record(*audited, "Updated", {"changed": [["description", "a", "b"]]}),
			5,
		)
		label = "audit trail: verify a record (longest chain)"
		yield (label, label, lambda: audit.verify_events(*audited), 3)

		def anchor() -> None:
			audit.record(*audited, "Updated", {"changed": [["description", "b", "c"]]})  # as each hour has
			audit_anchor.anchor_trail()

		yield ("audit trail: hourly anchor", "audit trail: hourly anchor", anchor, 3)
	yield ("audit trail: check the anchors", "audit trail: check the anchors", audit_anchor.verify_trail, 3)

	signed = longest_chain("Electronic Signature")
	if signed and frappe.db.exists("User", SIGNER):
		harness_signer()

		def sign_once() -> None:
			frappe.set_user(SIGNER)  # nosemgrep: frappe-setuser - test site only
			try:
				sign(
					frappe.get_doc(*signed),
					meaning="Verified",
					action="Volume harness check",
					password=frappe.local.volume_password,
					reason="Measured",
					permission="read",
				)
			finally:
				frappe.set_user("Administrator")  # nosemgrep: frappe-setuser - test site only

		label = "signature: sign (longest chain, with password check)"
		yield (label, label, sign_once, 3)
		label = "signature: verify a record (longest chain)"
		yield (label, label, lambda: verify_signatures(*signed), 3)


def page_and_job_paths() -> Iterator[Path]:
	from meddevice_suite.lot_control.report.lots_awaiting_release.lots_awaiting_release import (
		execute as lots_awaiting_release,
	)
	from meddevice_suite.lot_control.report.stock_by_lot_status.stock_by_lot_status import (
		execute as stock_by_lot_status,
	)
	from meddevice_suite.master_data import (
		customer_licence,
		item_master,
		master_approval,
		routing,
		supplier_approval,
	)
	from meddevice_suite.qms import change_control, document_control, training
	from meddevice_suite.qms.report.licence_register.licence_register import execute as licence_register
	from meddevice_suite.work_management import my_work, reminders

	yield ("My Work", "My Work", lambda: my_work.waiting_on("Administrator"), 3)

	settled(reminders.run)
	for name, category in reminders.CATEGORIES.items():
		yield (
			f"reminders: due items of {name}",
			"reminders: due items of a category",
			lambda c=category: list(c.due()),
			1,
		)
	yield ("reminders: daily run", "reminders: daily run", reminders.run, 1)

	def daily_alerts() -> None:
		supplier_approval.send_certificate_alerts()
		customer_licence.send_expiry_alerts()
		change_control.send_change_alerts()
		document_control.send_review_alerts()

	settled(daily_alerts)
	label = "alerts: daily supplier, licence, change and review alerts"
	yield (label, label, daily_alerts, 3)
	settled(lambda: training.reconcile(None))
	yield (
		"training: daily reconciliation",
		"training: daily reconciliation",
		lambda: training.reconcile(None),
		3,
	)

	staff = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=50)
	operations = frappe.get_all("Operation", pluck="name", limit=50)
	if staff and operations:
		pairs = {(person, operations[0]) for person in staff}
		label = "training: operators of a job card (50)"
		yield (label, label, lambda: training.not_competent(pairs), 5)
	codes = frappe.get_all("Item", filters={"name": ["like", f"{PREFIX}-ITEM-%"]}, pluck="name", limit=100)
	if codes:

		def items_of_a_record() -> None:
			entry = frappe.get_doc(
				{"doctype": "Stock Entry", "items": [{"item_code": c, "qty": 1} for c in codes]}
			)
			try:
				item_master.refuse_unapproved(entry)
			except frappe.ValidationError:
				pass  # a draft item among them is refused: the check ran in full

		label = "master data: items of a 100-line record"
		yield (label, label, items_of_a_record, 5)
	if operations:
		workstations = frappe.get_all("Workstation", pluck="name", limit=50)

		def operations_and_workstations() -> None:
			routing.not_usable(operations, workstations)
			master_approval.unapproved("Operation", operations)

		label = "master data: operations and workstations (50)"
		yield (label, label, operations_and_workstations, 5)
	yield ("report: Stock by Lot Status", "report: Stock by Lot Status", lambda: stock_by_lot_status({}), 3)
	yield (
		"report: Lots Awaiting Release",
		"report: Lots Awaiting Release",
		lambda: lots_awaiting_release({}),
		3,
	)
	yield ("report: Licence Register", "report: Licence Register", lambda: licence_register({}), 3)


def longest_chain(doctype: str) -> tuple[str, str] | None:
	"""The record with the most entries in the trail: the worst case of a chain's head and check."""
	from frappe.query_builder.functions import Count

	table = frappe.qb.DocType(doctype)
	rows = (
		frappe.qb.from_(table)
		.select(table.reference_doctype, table.reference_name, Count("*").as_("entries"))
		.groupby(table.reference_doctype, table.reference_name)
		.orderby(Count("*"), order=frappe.qb.desc)
		.limit(1)
		.run()
	)
	return (rows[0][0], rows[0][1]) if rows else None


def real_movement(item: str, company: str) -> tuple:
	"""A new lot of the item, released, received into the approved store, and one unit issued from it,
	all through ERPNext and this app's guards: its ledger entries, and the lot. Its expiry is the
	soonest of the item's lots in stock, so first expiry, first out lets it go while it reads every
	other lot of the item in the store."""
	from meddevice_suite.lot_control.lot_status import APPROVED, set_status

	store = approved_store(company)
	lot = frappe.get_doc({"doctype": "Batch", "item": item}).insert().name
	set_status(lot, APPROVED, "Released in the volume measurement")
	frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 7))  # measurement fixture
	receipt = stock_entry(
		company,
		"Material Receipt",
		[
			{
				"item_code": item,
				"qty": 10,
				"t_warehouse": store,
				"basic_rate": RATE,
				"batch_no": lot,
				"use_serial_batch_fields": 1,
			}
		],
	)
	issued = issue(item, lot, store)
	return ledger_entry(receipt), ledger_entry(issued), lot


def ledger_entry(voucher: str) -> frappe.model.document.Document:
	name = frappe.db.get_value("Stock Ledger Entry", {"voucher_no": voucher, "is_cancelled": 0}, "name")
	return frappe.get_doc("Stock Ledger Entry", name)


def issue(item: str, lot: str, store: str) -> str:
	return stock_entry(
		frappe.db.get_value("Warehouse", store, "company"),
		"Material Issue",
		[{"item_code": item, "qty": 1, "s_warehouse": store, "batch_no": lot, "use_serial_batch_fields": 1}],
	)


def stock_entry(company: str, purpose: str, rows: list[dict]) -> str:
	entry = frappe.get_doc(
		{"doctype": "Stock Entry", "stock_entry_type": purpose, "company": company, "items": rows}
	)
	entry.insert()
	entry.submit()
	return entry.name
