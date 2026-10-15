"""Query budgets (URS-PLT-021): the hot paths of the system issue a number of database queries that
does not grow with the records the site holds.

Each test runs a hot path on a small data set (SMALL records of the kind that piles up over the
years), counts the queries it issues, grows the data to LARGE records and runs the path again under
Frappe's own query budget (`IntegrationTestCase.assertQueryCount`), set to the count at the small
size. A query inside a loop over records makes the second run issue more and fails the test (rules.md
section 4: no queries inside loops over records); `TestTheBudgetCatchesALoop` shows that it does.

The paths run as each is used: the guards of a stock movement on its ledger entries, the audit trail's
write and its checks, a signature and its verification, My Work and the lot and licence reports, the
daily reminder, alert and training runs, and the production and master-data guards. A daily job is
measured as it runs on an ordinary day ("settled"): after a first run has raised and told what the
new records called for, which is one write per new item, not a read that grows.

The time each path takes on years of data is measured on a test site by the volume harness
(meddevice_suite/qa/volume.py), outside CI.
"""

import re
from collections.abc import Callable
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from meddevice_suite.compliance import audit, audit_anchor
from meddevice_suite.compliance.esign import sign, verify_signatures
from meddevice_suite.compliance.test_esign import PASSWORD, SIGNER, ensure_user, reset_lockout
from meddevice_suite.lot_control import expiry, finished_goods, lot_dates, lot_status, stores
from meddevice_suite.lot_control.lot_status import APPROVED, set_status
from meddevice_suite.lot_control.report.lots_awaiting_release.lots_awaiting_release import (
	execute as lots_awaiting_release,
)
from meddevice_suite.lot_control.report.stock_by_lot_status.stock_by_lot_status import (
	execute as stock_by_lot_status,
)
from meddevice_suite.lot_control.test_lot_status import ensure_parties, lot_item, stock_entry, store
from meddevice_suite.master_data import (
	customer_licence,
	equipment,
	item_master,
	master_approval,
	routing,
	supplier_approval,
	supplier_rating,
	warehouse,
)
from meddevice_suite.master_data.test_equipment import EquipmentTestCase
from meddevice_suite.master_data.test_supplier_approval import ensure_supplier, new_approval
from meddevice_suite.pilot.flow import lot_of
from meddevice_suite.qa import verifies
from meddevice_suite.qa.volume import APP_INDEXES, counted, index_present
from meddevice_suite.qms import change_control, document_control, equipment_care, pinned_records, training
from meddevice_suite.qms import device_master_record as dmr
from meddevice_suite.qms.report.licence_register.licence_register import execute as licence_register
from meddevice_suite.qms.test_change_control import licence_for_test
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.qms.test_training import TrainingTestCase, employee
from meddevice_suite.setup import indexes
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import (
	approve_equipment_for_test,
	approve_for_test,
	licensed_for_test,
	stock_item,
	work_instruction_for_test,
)
from meddevice_suite.work_management import my_work, reminders
from meddevice_suite.work_management.reminders import Due

# Records of each kind before and after the data grows: small, so the suite stays fast; five times
# apart, so a query per record shows as a dozen more queries.
SMALL, LARGE = 3, 15
GROWTH = LARGE - SMALL
# A stock entry's header and its lines, as the query text names their tables.
HEADER_READ = re.compile(r"from\s+`tabStock Entry`", re.IGNORECASE)
LINES_READ = re.compile(r"from\s+`tabStock Entry Detail`", re.IGNORECASE)


class QueryBudget:
	"""Mixed into an IntegrationTestCase: holds a hot path to the queries it issues on a small data
	set when the data has grown. The small count is measured as Frappe's `assertQueryCount` counts
	(`qa.volume.counted`), whose assertion takes only a fixed budget."""

	def assert_does_not_grow(
		self, call: Callable[[], object], grow: Callable[[], object], settle: bool = False
	) -> None:
		# Once first, so that what Frappe caches on first use (record type definitions, cached
		# values) is cached for both runs measured and they read alike.
		call()
		with counted() as small:
			call()
		grow()
		if settle:
			call()  # a daily job's first run after new records: what they call for is done once
		with self.assertQueryCount(len(small)):
			call()


@verifies("URS-PLT-021")
class TestTheBudgetCatchesALoop(QueryBudget, IntegrationTestCase):
	"""The check itself: a query per record fails it, one query for all of them does not."""

	def setUp(self):
		self.names = [self.todo() for _ in range(SMALL)]

	def todo(self) -> str:
		return frappe.get_doc({"doctype": "ToDo", "description": "Query budget check"}).insert().name

	def grow(self) -> None:
		self.names += [self.todo() for _ in range(GROWTH)]

	def test_a_query_per_record_fails_the_budget(self):
		with self.assertRaises(AssertionError):
			self.assert_does_not_grow(lambda: [frappe.db.exists("ToDo", n) for n in self.names], self.grow)

	def test_one_query_for_all_records_keeps_within_it(self):
		self.assert_does_not_grow(
			lambda: frappe.get_all("ToDo", filters={"name": ["in", self.names]}, pluck="name"), self.grow
		)


@verifies("URS-PLT-021")
class TestStockMovementBudget(QueryBudget, IntegrationTestCase):
	"""Every stock movement passes the guards on its ledger entries (master_data/warehouse.py,
	lot_control/finished_goods.py, lot_status.py, expiry.py, lot_dates.py). The data that grows is
	the item's history: lots made every day, used up or sitting in another store, that expire before
	the lot moved, which first expiry, first out has to look past."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		ensure_parties()

	def setUp(self):
		frappe.set_user("Administrator")
		self.item = lot_item(f"MDS-QB-{frappe.generate_hash(length=6)}")
		approve_for_test(self.item)
		self.stores, self.elsewhere = store("Stores"), store("Finished Goods")
		self.history: list[str] = []
		self.history_bundles: list[str] = []
		self.grow_history(SMALL)
		# The lot moved: the latest made, expiring last, in the store it is used from.
		lots, receipt = self.approved_lots(1, self.stores, expires_in_days=2000)
		self.lot = lots[0]
		issue = stock_entry(
			"Material Issue",
			[
				{
					"item_code": self.item,
					"qty": 1,
					"s_warehouse": self.stores,
					"batch_no": self.lot,
					"use_serial_batch_fields": 1,
				}
			],
		)
		self.coming_in, self.going_out = self.ledger_entry(receipt), self.ledger_entry(issue.name)

	def approved_lots(self, count: int, warehouse: str, expires_in_days: int) -> tuple[list[str], str]:
		"""New lots of the item, released as QA's release leaves them (test_lot_status.receive) and
		received on one material receipt into `warehouse`; and the receipt."""
		lots = []
		for i in range(count):
			lot = frappe.get_doc({"doctype": "Batch", "item": self.item}).insert().name
			set_status(lot, APPROVED, "Approved for the test, as QA's release leaves it")
			# Test fixture: each lot made a day after the one before it.
			frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), expires_in_days + i))
			lots.append(lot)
		receipt = stock_entry(
			"Material Receipt",
			[
				{
					"item_code": self.item,
					"qty": 5,
					"t_warehouse": warehouse,
					"basic_rate": 10,
					"batch_no": lot,
					"use_serial_batch_fields": 1,
				}
				for lot in lots
			],
		)
		return lots, receipt.name

	def grow_history(self, count: int) -> None:
		lots, receipt = self.approved_lots(count, self.elsewhere, expires_in_days=30 + len(self.history))
		self.history += lots
		self.history_bundles += frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": receipt, "serial_and_batch_bundle": ["is", "set"]},
			pluck="serial_and_batch_bundle",
		)

	def ledger_entry(self, voucher: str) -> frappe.model.document.Document:
		name = frappe.db.get_value("Stock Ledger Entry", {"voucher_no": voucher, "is_cancelled": 0}, "name")
		return frappe.get_doc("Stock Ledger Entry", name)

	def guards(self) -> None:
		for entry in (self.coming_in, self.going_out):
			warehouse.guard_stock_ledger_entry(entry)
			finished_goods.guard_stock_ledger_entry(entry)
			lot_status.guard_stock_ledger_entry(entry)
			expiry.guard_stock_ledger_entry(entry)
		lot_dates.has_stock_history(self.lot)

	def test_the_guards_of_a_movement_do_not_grow_with_the_item_s_lots(self):
		self.assert_does_not_grow(self.guards, lambda: self.grow_history(GROWTH))

	def test_a_document_s_header_is_read_once_whatever_its_lines(self):
		def reads_of(lines: int) -> tuple[int, int]:
			"""How often the guards of all the ledger entries of a document of `lines` lines read its
			header and its lines, as ERPNext validates them one after another in one submission."""
			_lots, receipt = self.approved_lots(lines, self.elsewhere, expires_in_days=400)
			entries = [
				frappe.get_doc("Stock Ledger Entry", name)
				for name in frappe.get_all(
					"Stock Ledger Entry", filters={"voucher_no": receipt, "is_cancelled": 0}, pluck="name"
				)
			]
			self.assertEqual(len(entries), lines)
			lot_status.forget_vouchers()  # as at the start of a submission
			with counted() as queries:
				for entry in entries:
					warehouse.guard_stock_ledger_entry(entry)
					finished_goods.guard_stock_ledger_entry(entry)
					lot_status.guard_stock_ledger_entry(entry)
					expiry.guard_stock_ledger_entry(entry)
			return (
				sum(1 for query in queries if HEADER_READ.search(query)),
				sum(1 for query in queries if LINES_READ.search(query)),
			)

		self.assertEqual(reads_of(SMALL), (1, 1))
		self.assertEqual(reads_of(LARGE), (1, 1))

	def test_a_stock_count_reads_its_lots_at_once(self):
		count = frappe._dict(
			voucher_type="Stock Reconciliation",
			voucher_no="MDS-QB-NO-COUNT",
			voucher_detail_no="MDS-QB-NO-LINE",
			batch_no=None,
			serial_and_batch_bundle="MDS-QB-NO-BUNDLE",
			actual_qty=1,
		)
		self.assert_does_not_grow(
			lambda: lot_status.counted_back(count, self.history), lambda: self.grow_history(GROWTH)
		)

	def test_a_document_s_lines_read_their_lots_at_once(self):
		def lines() -> list[frappe._dict]:
			# Lines naming their lots only by bundle, as ERPNext v16 records them.
			return [frappe._dict(serial_and_batch_bundle=bundle) for bundle in self.history_bundles]

		self.assert_does_not_grow(lambda: lot_status.lots_of_rows(lines()), lambda: self.grow_history(GROWTH))
		self.assertEqual(len(self.history_bundles), LARGE)


@verifies("URS-PLT-021")
class TestAuditTrailBudget(QueryBudget, IntegrationTestCase):
	"""Every save of a regulated record writes an audit event chained to the record's last one;
	reviewers verify a record's chain, and the hourly job anchors and checks the whole trail."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.item = stock_item(f"MDS-QB-AUDIT-{frappe.generate_hash(length=6)}")
		self.events(SMALL)

	def events(self, count: int) -> None:
		for i in range(count):
			audit.record("Item", self.item, "Updated", {"changed": [["description", str(i), str(i + 1)]]})

	def test_writing_and_verifying_an_event_does_not_grow_with_the_record_s_history(self):
		def write_and_verify() -> None:
			self.events(1)
			audit.verify_events("Item", self.item)

		self.assert_does_not_grow(write_and_verify, lambda: self.events(GROWTH))
		self.assertEqual(audit.verify_events("Item", self.item), [])

	def test_anchoring_and_checking_the_trail_does_not_grow_with_it(self):
		def anchor_and_check() -> None:
			self.events(1)  # an entry since the last anchor, as each hour has
			audit_anchor.anchor_trail()
			audit_anchor.verify_trail()

		# Twice before measuring: the first anchor of the test, and the first check after it (which
		# may alert a finding of this site once), are done before the runs measured.
		anchor_and_check()
		self.assert_does_not_grow(anchor_and_check, lambda: self.events(GROWTH))


@verifies("URS-PLT-021")
class TestSignatureBudget(QueryBudget, IntegrationTestCase):
	"""A signature is chained to the record's last one; its chain is verified on review."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SIGNER, "Signer", ["System Manager"])

	def setUp(self):
		reset_lockout(SIGNER)
		frappe.set_user(SIGNER)
		self.todo = frappe.get_doc({"doctype": "ToDo", "description": "Query budget signatures"}).insert()
		self.signatures(SMALL)

	def tearDown(self):
		frappe.set_user("Administrator")
		reset_lockout(SIGNER)

	def signatures(self, count: int) -> None:
		for _ in range(count):
			sign(self.todo, meaning="Approved", action="Stock Release", password=PASSWORD)

	def test_signing_and_verifying_does_not_grow_with_the_record_s_signatures(self):
		def sign_and_verify() -> None:
			self.signatures(1)
			verify_signatures("ToDo", self.todo.name)

		self.assert_does_not_grow(sign_and_verify, lambda: self.signatures(GROWTH))
		self.assertTrue(verify_signatures("ToDo", self.todo.name))


@verifies("URS-PLT-021")
class TestPagesAndReportsBudget(QueryBudget, IntegrationTestCase):
	"""My Work, opened by everyone many times a day, and the lot and licence reports, as lots in
	quarantine, open reminders, items in draft and licences pile up."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		ensure_parties()

	def setUp(self):
		frappe.set_user("Administrator")
		self.item = lot_item(f"MDS-QB-PAGE-{frappe.generate_hash(length=6)}")
		approve_for_test(self.item)
		self.grow(SMALL)

	def grow(self, count: int) -> None:
		"""Lots in quarantine with an open reminder each, items in draft, licences of the plant."""
		receipt = stock_entry(
			"Material Receipt",
			[
				{"item_code": self.item, "qty": 5, "t_warehouse": store(stores.QUARANTINE), "basic_rate": 10}
				for _ in range(count)
			],
		)
		for row in receipt.items:
			lot = lot_of(row)
			reminders.make(
				"Lot Expiry",
				Due("Batch", lot, add_days(today(), 5), f"Lot {lot} expires (query budget)", ("Stock User",)),
			)
		for _ in range(count):
			stock_item(f"MDS-QB-DRAFT-{frappe.generate_hash(length=6)}")
			licence_for_test()

	def test_my_work_does_not_grow_with_what_waits(self):
		self.assert_does_not_grow(lambda: my_work.waiting_on("Administrator"), lambda: self.grow(GROWTH))

	def test_the_lot_reports_do_not_grow_with_the_lots(self):
		self.assert_does_not_grow(
			lambda: (stock_by_lot_status({}), lots_awaiting_release({})), lambda: self.grow(GROWTH)
		)

	def test_the_licence_register_does_not_grow_with_the_licences(self):
		self.assert_does_not_grow(lambda: licence_register({}), lambda: self.grow(GROWTH))


@verifies("URS-PLT-021")
class TestDailyJobsBudget(QueryBudget, IntegrationTestCase):
	"""The daily reminder and alert runs read every record still due; the records that stay due for
	good (a supplier decision replaced, a licence renewed, an action of a change never approved, a
	document already alerted, a reminder nobody completes) must not cost a query each, every day."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		ensure_parties()
		cls.requester = signer("Production Supervisor")

	def setUp(self):
		frappe.set_user("Administrator")
		self.item = lot_item(f"MDS-QB-DAILY-{frappe.generate_hash(length=6)}")
		approve_for_test(self.item)
		frappe.set_user(self.requester)  # a change is raised by its requester
		self.change = (
			frappe.get_doc(
				{
					"doctype": "Change Request",
					"title": "Query budget change, never approved",
					"change_type": "Document",
					"description": "Left in draft by the test",
					"reason": "Query budget",
					"affected_records": [
						{
							"reference_doctype": "Controlled Document",
							"reference_name": work_instruction_for_test(),
						}
					],
					"actions": [
						{"description": "First action", "action_owner": self.requester, "due_date": today()}
					],
				}
			)
			.insert()
			.name
		)
		frappe.set_user("Administrator")
		self.grow(SMALL)

	def grow(self, count: int) -> None:
		for _ in range(count):
			self.replaced_supplier_decision()
			self.renewed_licence()
			self.undone_action()
			self.document_alerted_for_review()
			self.reminder_left_escalated()
			self.recurring_activity()
		self.lots_expiring(count)

	def replaced_supplier_decision(self) -> None:
		"""A supplier whose decision with a certificate expiring soon was replaced by a newer one."""
		supplier = f"MDS QB Supplier {frappe.generate_hash(length=6)}"
		ensure_supplier(supplier)
		for expires_in, decided_days_ago in ((5, 30), (365, 1)):
			name = (
				new_approval(supplier, [self.item], certificate_expiry=add_days(today(), expires_in))
				.insert()
				.name
			)
			# Test fixture: recorded as QA's signed decision leaves it (tested in test_supplier_approval).
			frappe.db.set_value(
				"Supplier Approval",
				name,
				{
					"docstatus": 1,
					"decision": supplier_approval.APPROVED,
					"decided_at": add_days(now_datetime(), -decided_days_ago),
				},
			)

	def renewed_licence(self) -> None:
		"""A customer whose licence ending soon has its renewal verified already."""
		customer = f"MDS QB Customer {frappe.generate_hash(length=6)}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer,
				"customer_group": "Commercial",
				"territory": "India",
			}
		).insert()
		ending = licensed_for_test(customer)
		frappe.db.set_value(customer_licence.DOCTYPE, ending, "valid_until", add_days(today(), 5))  # fixture
		licensed_for_test(customer)  # the renewal, verified

	def undone_action(self) -> None:
		"""An action due on a change that was never approved: it is never alerted nor reminded."""
		frappe.get_doc(
			{
				"doctype": "Change Request Action",
				"parent": self.change,
				"parenttype": "Change Request",
				"parentfield": "actions",
				"description": "Action of a change never approved (query budget)",
				"action_owner": self.requester,
				"due_date": today(),
			}
		).db_insert()  # test fixture: a row of the draft request

	def document_alerted_for_review(self) -> None:
		"""An effective document whose review falls due soon, alerted for it already."""
		doc = frappe.get_doc(
			{
				"doctype": "Controlled Document",
				"title": f"Query budget document {frappe.generate_hash(length=6)}",
				"document_type": "SOP",
				"department": "QA",
				"process_owner": "Administrator",
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		due = add_days(today(), 5)
		frappe.db.set_value(  # test fixture: as an approved revision and its alert leave it
			"Controlled Document",
			doc.name,
			{"status": "Effective", "next_review_date": due, "review_alerted_for": due},
		)

	def reminder_left_escalated(self) -> None:
		"""A recurring occurrence nobody completed, escalated long ago."""
		reference = frappe.get_doc({"doctype": "ToDo", "description": "Query budget occurrence"}).insert()
		reminders.make(
			reminders.SCHEDULED,
			Due(
				"ToDo",
				reference.name,
				add_days(today(), -60),
				"Occurrence left undone (query budget)",
				("HR User",),
			),
		)

	def recurring_activity(self) -> None:
		"""A recurring activity QA plans, its next occurrence within its lead time: raised once, then
		found raised already every day until it is done (configuration, read in one query)."""
		frappe.get_doc(
			{
				"doctype": "Reminder Schedule",
				"title": f"Query budget check {frappe.generate_hash(length=6)}",
				"description": "Recurring activity (query budget)",
				"responsible_role": "Stock User",
				"frequency": "Monthly",
				"first_due_date": add_days(today(), 3),
				"lead_days": 5,
			}
		).insert()

	def lots_expiring(self, count: int) -> None:
		receipt = stock_entry(
			"Material Receipt",
			[
				{"item_code": self.item, "qty": 5, "t_warehouse": store(stores.QUARANTINE), "basic_rate": 10}
				for _ in range(count)
			],
		)
		for row in receipt.items:
			frappe.db.set_value("Batch", lot_of(row), "expiry_date", add_days(today(), 5))  # test fixture

	def test_each_reminder_category_reads_alike_whatever_stays_due(self):
		self.assert_does_not_grow(
			lambda: [list(category.due()) for category in reminders.CATEGORIES.values()],
			lambda: self.grow(GROWTH),
		)

	def test_the_daily_reminder_run_reads_alike_on_an_ordinary_day(self):
		self.assert_does_not_grow(reminders.run, lambda: self.grow(GROWTH), settle=True)

	def test_the_daily_alerts_read_alike_on_an_ordinary_day(self):
		def alerts() -> None:
			supplier_approval.send_certificate_alerts()
			customer_licence.send_expiry_alerts()
			change_control.send_change_alerts()
			document_control.send_review_alerts()

		self.assert_does_not_grow(alerts, lambda: self.grow(GROWTH), settle=True)

	def test_the_monthly_supplier_ratings_read_what_is_rated_at_once(self):
		suppliers = [f"MDS QB Rated {i}" for i in range(SMALL)]

		def rate() -> None:
			# The suppliers with lots or order lines in each month; the rating itself is a write per
			# supplier and month, made once, and tested in test_supplier_rating.
			with (
				patch.object(
					supplier_rating,
					"lots_decided",
					lambda *_args: [frappe._dict(supplier=s) for s in suppliers],
				),
				patch.object(supplier_rating, "lines_due", lambda *_args: []),
				patch.object(supplier_rating, "rate", lambda *_args: None),
			):
				supplier_rating.rate_finished_months()

		self.assert_does_not_grow(
			rate, lambda: suppliers.extend(f"MDS QB Rated {i}" for i in range(SMALL, LARGE))
		)


@verifies("URS-PLT-021")
class TestPinnedRecordSweepBudget(QueryBudget, IntegrationTestCase):
	"""The daily sweep of pinned records finds every device master record out of date again, each
	day until QA re-approves it: what it has recorded already is read for all of them at once, and
	none is loaded or locked again for nothing."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.item = stock_item(f"MDS-QB-PIN-{frappe.generate_hash(length=6)}")
		approve_for_test(self.item)
		self.records: list[str] = []
		self.out_of_date(SMALL)

	def out_of_date(self, count: int) -> None:
		"""Device master records out of date, their device element's new version recorded already, as
		the sweep leaves them (test fixture: drafting and approving one is tested in
		qms/test_device_master_record.py)."""
		for _ in range(count):
			record = f"DMR-QB-{frappe.generate_hash(length=8)}"
			self.records.append(record)
			frappe.get_doc(
				{
					"doctype": dmr.DOCTYPE,
					"name": record,
					"item": self.item,
					"revision": "01",
					"status": dmr.OUT_OF_DATE,
					"out_of_date_since": now_datetime(),
					"docstatus": 1,
				}
			).db_insert()
			row = frappe.get_doc(
				{
					"doctype": dmr.ELEMENT,
					"parent": record,
					"parenttype": dmr.DOCTYPE,
					"parentfield": "elements",
					"idx": 1,
					"docstatus": 1,
					"element_type": dmr.DEVICE,
					"reference_doctype": "Item",
					"reference_name": self.item,
					"pinned_version": "0",  # approved against a version before the item's own
				}
			)
			row.db_insert()
			changes = pinned_records.changes_of(
				[pinned_records.Pin(record, "Item", self.item, "0", row.name)]
			)
			((note, changed_under),) = dmr.notes_of(changes)
			frappe.db.set_value(
				dmr.ELEMENT,
				row.name,
				{
					"stale": 1,
					"current_version": changes[0].current,
					"change_note": note,
					"current_changed_under": changed_under,
				},
			)

	def test_the_daily_sweep_does_not_reload_what_it_recorded(self):
		self.assert_does_not_grow(pinned_records.sweep, lambda: self.out_of_date(GROWTH))
		ours = [p for p in dmr.element_pins(None) if p.owner in self.records]
		self.assertEqual(len(ours), LARGE)
		self.assertEqual(dmr.unrecorded(pinned_records.changes_of(ours)), [])  # all recorded already


@verifies("URS-PLT-021")
class TestTrainingBudget(QueryBudget, TrainingTestCase):
	"""The daily training run reconciles every employee's needs, and every job card checks that its
	operators are competent in the operation's work instruction."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.document, _revision = self.sop("Work Instruction", "PRD")
		self.job = self.job_with(self.document)
		self.operation = (
			frappe.get_doc(
				{
					"doctype": "Operation",
					"name": f"MDS QB OP {frappe.generate_hash(length=6)}",
					routing.WORK_INSTRUCTION: self.document.name,
				}
			)
			.insert()
			.name
		)
		self.staff: list[str] = []
		self.hire(SMALL)

	def hire(self, count: int) -> None:
		# Each joins the job: their training is opened as they join (training.on_employee_update).
		self.staff += [employee(self.job) for _ in range(count)]

	def test_the_daily_reconciliation_reads_alike_whatever_the_staff(self):
		self.assert_does_not_grow(lambda: training.reconcile(None), lambda: self.hire(GROWTH), settle=True)

	def test_the_operators_of_a_job_card_are_checked_at_once(self):
		def check() -> list[str]:
			return training.not_competent({(person, self.operation) for person in self.staff})

		self.assert_does_not_grow(check, lambda: self.hire(GROWTH))
		self.assertEqual(len(check()), LARGE)  # none of them trained yet: each refused, by name


@verifies("URS-PLT-021")
class TestEquipmentBudget(QueryBudget, EquipmentTestCase):
	"""Every job card save, and every inspection reading, judges the equipment at the workstation or
	the instruments used: all of it at once, however much equipment the plant holds."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.workstation = (
			frappe.get_doc(
				{"doctype": "Workstation", "workstation_name": f"MDS QB WS {frappe.generate_hash(length=6)}"}
			)
			.insert()
			.name
		)
		self.assets: list[str] = []
		self.install(SMALL)

	def install(self, count: int) -> None:
		for _ in range(count):
			asset = self.new_equipment(**{equipment.WORKSTATION: self.workstation}).name
			approve_equipment_for_test(asset)  # approved and in date, as the signed acts leave it
			self.assets.append(asset)

	def test_the_equipment_at_a_workstation_is_judged_at_once(self):
		self.assert_does_not_grow(
			lambda: equipment_care.refuse_unusable([self.workstation], "A job card"),
			lambda: self.install(GROWTH),
		)

	def test_the_instruments_of_an_inspection_are_judged_at_once(self):
		self.assert_does_not_grow(lambda: equipment.usable_all(self.assets), lambda: self.install(GROWTH))
		self.assertTrue(all(state.ok for state in equipment.usable_all(self.assets).values()))


@verifies("URS-PLT-021")
class TestMasterDataBudget(QueryBudget, IntegrationTestCase):
	"""Every record that names items, operations or workstations checks they are approved: in one
	query for all of them, however many lines the record has."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.items: list[str] = []
		self.operations: list[str] = []
		self.add(SMALL)

	def add(self, count: int) -> None:
		for _ in range(count):
			code = stock_item(f"MDS-QB-MST-{frappe.generate_hash(length=6)}")
			approve_for_test(code)
			self.items.append(code)
			self.operations.append(
				frappe.get_doc(
					{
						"doctype": "Operation",
						"name": f"MDS QB MST OP {frappe.generate_hash(length=6)}",
						routing.WORK_INSTRUCTION: work_instruction_for_test(),
					}
				)
				.insert()
				.name
			)

	def test_a_record_s_items_are_checked_at_once(self):
		def check() -> None:
			entry = frappe.get_doc(
				{"doctype": "Stock Entry", "items": [{"item_code": code, "qty": 1} for code in self.items]}
			)
			item_master.refuse_unapproved(entry)

		self.assert_does_not_grow(check, lambda: self.add(GROWTH))

	def test_operations_are_checked_at_once(self):
		self.assert_does_not_grow(
			lambda: (
				routing.not_usable(self.operations, []),
				master_approval.unapproved("Operation", self.operations),
			),
			lambda: self.add(GROWTH),
		)


@verifies("URS-PLT-021")
class TestIndexes(IntegrationTestCase):
	"""The filters the hot paths use on growing tables are served by an index: the app's own tables,
	and the columns of ERPNext's that the app indexes by configuration (setup/indexes.py), which must
	outlive an ERPNext upgrade. A dropped index fails here."""

	def test_the_app_s_growing_tables_are_indexed(self):
		for doctype, columns in APP_INDEXES:
			with self.subTest(doctype=doctype, columns=columns):
				self.assertTrue(index_present(doctype, columns), f"{doctype} has no index on {columns}")

	def test_the_indexes_on_erpnext_s_tables_outlive_an_upgrade(self):
		# Marked indexed in the record type's definition, so Frappe's schema sync keeps them.
		for doctype, field in indexes.SEARCHED:
			with self.subTest(doctype=doctype, field=field):
				self.assertEqual(frappe.get_meta(doctype).get_field(field).search_index, 1)

	def test_applying_the_indexes_again_changes_nothing(self):
		setters = frappe.db.count("Property Setter", {"property": "search_index"})
		indexes.apply_indexes()
		self.assertEqual(frappe.db.count("Property Setter", {"property": "search_index"}), setters)
