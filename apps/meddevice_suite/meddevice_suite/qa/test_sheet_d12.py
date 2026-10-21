"""The data the browser test sheet of D12 Audit Logs starts from (qa/sheets/d12.py), proven on its own.

The sheet's Cypress spec (cypress/integration/sheet_d12.js) calls these set-ups as the Administrator
of a test site, after ui_journey.prepare_journey, and its tests start from what they make. These
tests make the same calls the same way and check that every record is there, in the state the sheet
starts from, made by the job that makes it with its signatures; that the tampering of D12-T22 is
reported by the anchors and that the trail is whole again once restored; that the evidence records
refuse every write past permissions; and that every set-up is refused off a test site, to anyone but
the Administrator, and without the run's password where it signs.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_days, today

from meddevice_suite.compliance import audit_anchor, quality_settings
from meddevice_suite.compliance.audit import verify_events
from meddevice_suite.master_data import equipment
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d12
from meddevice_suite.qa.test_sheets import SheetTestCase, signed
from meddevice_suite.qa.ui_journey import SUPPLIER, prepare_journey
from meddevice_suite.qa.ui_users import email_for, prepare_job_users

QA, PURCHASE, MANAGEMENT, MAINTENANCE = "QA Manager", "Purchase", "Management", "Maintenance"


def set_ups(password: str) -> list[tuple]:
	"""Every set-up the sheet offers the browser, with arguments of the kind it sends."""
	return [
		(d12.prepare_sheet, {"password": password}),
		(d12.prepare_purchase_order, {"password": password, "stage": "draft"}),
		(d12.prepare_instrument, {"password": password}),
		(d12.prepare_change_awaiting_approval, {"password": password}),
		(d12.prepare_quality_settings, {"password": password}),
		(d12.prepare_released_lot, {"password": password}),
		(d12.prepare_archive, {}),
		(d12.archive_contents, {"archive": "ARC-ANY"}),
		(d12.prepare_trail_record, {}),
		(d12.remove_audit_events, {"name": f"{d12.TRAIL_PREFIX} ANY", "newest": 1}),
		(d12.restore_audit_trail, {}),
		(d12.refusals_past_permissions, {"doctype": "Audit Event"}),
	]


@verifies("URS-PLT-007")
class TestSheetD12SetUpRefusals(SheetTestCase):
	def test_every_set_up_the_sheet_offers_is_checked_here(self):
		offered = {
			value
			for value in vars(d12).values()
			if callable(value)
			and getattr(value, "__module__", None) == d12.__name__
			and value in frappe.whitelisted
		}
		self.assertEqual(offered, {set_up for set_up, _arguments in set_ups(self.password)})

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			for set_up, arguments in set_ups(self.password):
				with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
					set_up(**arguments)

	def test_only_the_administrator_prepares_the_sheet(self):
		frappe.set_user(prepare_job_users()["users"][QA])

		for set_up, arguments in set_ups(self.password):
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
				set_up(**arguments)

	def test_refuses_to_sign_without_the_runs_password(self):
		without = [
			d12.prepare_sheet,
			d12.prepare_purchase_order,
			d12.prepare_instrument,
			d12.prepare_change_awaiting_approval,
			d12.prepare_quality_settings,
			d12.prepare_released_lot,
		]
		made = ("Purchase Order", "Asset", "Change Request", "Purchase Receipt")
		before = {doctype: frappe.db.count(doctype) for doctype in made}
		for set_up in without:
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.ValidationError):
				set_up(password="")
		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in made}, before)  # refused first

	def test_refuses_what_the_sheet_does_not_prepare(self):
		orders = frappe.db.count("Purchase Order")
		with self.assertRaises(frappe.ValidationError):
			d12.prepare_purchase_order(self.password, "cancelled")
		self.assertEqual(frappe.db.count("Purchase Order"), orders)
		with self.assertRaises(frappe.ValidationError):
			d12.refusals_past_permissions("Customer")  # not an evidence record

	def test_tampers_only_with_the_sheets_own_workstations(self):
		events = frappe.db.count("Audit Event")
		other = frappe.get_all("Audit Event", fields=["reference_name"], limit=1)
		for name in ("UIJ Moulding Station", other[0].reference_name if other else "Any record"):
			with self.subTest(name=name), self.assertRaises(frappe.ValidationError):
				d12.remove_audit_events(name, newest=1)
		self.assertEqual(frappe.db.count("Audit Event"), events)


@verifies("URS-PLT-007")
class TestSheetD12AuditLogs(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d12.prepare_sheet(password)

		cls.sheet = cls.prepared(before)

	def tearDown(self):
		frappe.cache.delete_value(d12.REMOVED_KEY)  # the cache is not rolled back with the test
		super().tearDown()

	def test_names_what_the_sheet_works_with(self):
		self.assertEqual((self.sheet["supplier"], self.sheet["material"]), (SUPPLIER, d12.MATERIAL))
		self.assertEqual(self.sheet["names"][PURCHASE], frappe.utils.get_fullname(email_for(PURCHASE)))
		self.assertTrue(frappe.db.exists("Warehouse", self.sheet["quarantine"]))

	def test_a_purchase_order_in_draft_and_one_approved_by_management(self):
		draft = frappe.get_doc("Purchase Order", d12.prepare_purchase_order(self.password)["order"])
		approved = frappe.get_doc(
			"Purchase Order", d12.prepare_purchase_order(self.password, "approved")["order"]
		)

		self.assertEqual((draft.docstatus, draft.owner, draft.items[0].qty), (0, email_for(PURCHASE), 10))
		self.assertEqual((approved.docstatus, approved.workflow_state), (1, "Approved"))
		self.assertEqual(approved.owner, email_for(PURCHASE))

	def test_an_instrument_ready_for_qa_and_one_calibrated_with_a_signature(self):
		ready = d12.prepare_instrument(self.password)["asset"]
		made = d12.prepare_instrument(self.password, calibrated=1)

		self.assertEqual(frappe.db.get_value("Asset", ready, equipment.APPROVAL_STATUS), equipment.DRAFT)
		self.assertEqual(signed("Asset", ready), [])
		self.assertEqual(
			frappe.db.get_value("Calibration Record", made["calibration"], ["asset", "docstatus"]),
			(made["asset"], 1),
		)
		self.assertEqual(
			signed("Calibration Record", made["calibration"]), [("Complete", email_for(MAINTENANCE))]
		)

	def test_a_change_awaiting_qas_approval_assessed_with_a_signature(self):
		made = d12.prepare_change_awaiting_approval(self.password)

		request = frappe.get_doc("Change Request", made["change_request"])
		self.assertEqual(request.status, "Awaiting Approval")
		self.assertEqual(
			[(row.reference_doctype, row.reference_name) for row in request.affected_records],
			[(d12.SETTINGS, d12.SETTINGS)],
		)
		self.assertEqual(signed("Change Request", request.name), [("Assess", email_for(QA))])
		self.assertEqual(frappe.db.get_value("Electronic Signature", made["assessment"], "action"), "Assess")

	def test_quality_settings_brought_back_and_an_approved_request_listing_them(self):
		request = d12.settings_change(self.password)
		with d12.act_as(QA):
			settings = frappe.get_single(d12.SETTINGS)
			settings.update({"login_lockout_attempts": 4, "change_request": request})
			settings.save()

		made = d12.prepare_quality_settings(self.password)

		for field in d12.SHEET_SETTINGS:
			self.assertEqual(quality_settings.value(field), quality_settings.SETTINGS[field].default, field)
		self.assertEqual(made["values"]["login_lockout_attempts"], 5)
		self.assertEqual(frappe.db.get_value("Change Request", made["change_request"], "status"), "Approved")
		self.assertEqual(made["version"], quality_settings.stored(quality_settings.VERSION))

	def test_a_lot_released_by_qa_with_a_signature(self):
		made = d12.prepare_released_lot(self.password)

		self.assertEqual(frappe.db.get_value("Lot Release", made["release"], "batch"), made["lot"])
		self.assertIn(("Release", email_for(QA)), signed("Lot Release", made["release"]))

	def test_an_archive_of_a_day_long_past_requested_by_qa(self):
		archive = frappe.get_doc(d12.ARCHIVE, d12.prepare_archive()["archive"])

		day = add_days(today(), d12.ARCHIVED_DAY)
		self.assertEqual(
			(archive.owner, str(archive.from_date), str(archive.to_date)), (email_for(QA), day, day)
		)
		self.assertEqual(archive.status, "Completed")  # built at once in a test (Record Archive.after_insert)
		contents = d12.archive_contents(archive.name)
		self.assertTrue({"manifest.json", "index.csv", "logins.csv"} <= set(contents["files"]))
		self.assertIn("record_counts", contents["manifest_keys"])

	def test_a_workstation_with_several_audit_events(self):
		made = d12.prepare_trail_record()

		self.assertTrue(made["name"].startswith(d12.TRAIL_PREFIX))
		actions = frappe.get_all(
			"Audit Event",
			filters={"name": ["in", made["events"]]},
			pluck="action",
			order_by="creation asc, name asc",
		)
		self.assertGreaterEqual(len(actions), 4)
		self.assertEqual(actions[0], "Created")
		self.assertEqual(set(actions[1:]), {"Updated"})
		self.assertEqual(verify_events(d12.TRAIL_DOCTYPE, made["name"]), [])

	def test_the_newest_entries_removed_are_reported_and_put_back(self):
		made = d12.prepare_trail_record()
		audit_anchor.anchor_trail(audit_anchor.ON_REQUEST)

		removed = d12.remove_audit_events(made["name"], newest=2)

		self.assertEqual(removed["removed"], made["events"][-2:])
		self.assertEqual(removed["events_left"], made["events"][:-2])
		self.assertTrue(removed["head_removed"])
		self.assertEqual(verify_events(d12.TRAIL_DOCTYPE, made["name"]), [])  # a shorter chain verifies
		anchor = removed["anchor"]["name"]
		found = audit_anchor.verify_trail()
		self.assertIn(
			f"Audit Event: entry {made['events'][-1]}, the newest when anchor {anchor} was recorded, is "
			"missing: the newest entries have been removed.",
			found,
		)
		self.assertIn(
			f"Audit Event: 2 of the {removed['anchor']['entry_count']} entries recorded up to anchor "
			f"{anchor} are missing.",
			found,
		)

		audit_anchor.anchor_trail(audit_anchor.ON_REQUEST)  # carries the removal forward
		d12.remove_audit_events(made["name"], oldest=1)
		restored = d12.restore_audit_trail()

		self.assertEqual(sorted(restored["restored"]), sorted([*made["events"][-2:], made["events"][0]]))
		self.assertTrue(restored["anchors_removed"])
		self.assertEqual(d12.events_of(made["name"]), made["events"])
		self.assertEqual(verify_events(d12.TRAIL_DOCTYPE, made["name"]), [])
		self.assertEqual(audit_anchor.newest_anchor("Audit Event").name, anchor)
		self.assertFalse([problem for problem in audit_anchor.verify_trail() if anchor in problem])
		self.assertEqual(d12.restore_audit_trail(), {"restored": [], "anchors_removed": []})

	def test_keeps_one_audit_event_of_the_workstation(self):
		made = d12.prepare_trail_record()

		with self.assertRaises(frappe.ValidationError):
			d12.remove_audit_events(made["name"], newest=len(made["events"]))
		self.assertEqual(d12.events_of(made["name"]), made["events"])

	def test_the_evidence_records_refuse_every_write_past_permissions(self):
		made = d12.prepare_trail_record()
		audit_anchor.anchor_trail(audit_anchor.ON_REQUEST)
		event = made["events"][0]
		signature = frappe.get_all("Electronic Signature", pluck="name", limit=1)[0]
		anchor = audit_anchor.newest_anchor("Audit Event").name
		archive = d12.prepare_archive()["archive"]

		self.assertEqual(
			d12.refusals_past_permissions("Audit Event", event),
			{
				"insert": "An audit event is recorded only by the audit trail.",
				"change": "An audit event cannot be changed.",
				"delete": "An audit event cannot be deleted.",
			},
		)
		self.assertEqual(
			d12.refusals_past_permissions("Electronic Signature", signature),
			{
				"insert": "An electronic signature is recorded only by signing, with the signer's password.",
				"change": "An electronic signature cannot be changed.",
				"delete": "An electronic signature cannot be deleted.",
			},
		)
		self.assertEqual(
			d12.refusals_past_permissions("Audit Anchor", anchor),
			{
				"insert": "An audit anchor is recorded only by the audit trail's anchoring.",
				"change": "An audit anchor cannot be changed.",
				"delete": "An audit anchor cannot be deleted.",
			},
		)
		self.assertEqual(
			d12.refusals_past_permissions("Audit Trail Finding"),
			{"insert": "An audit trail finding is recorded only by the audit trail's anchoring."},
		)
		self.assertEqual(
			d12.refusals_past_permissions(d12.ARCHIVE, archive),
			{
				"change": "A record archive cannot be changed.",
				"delete": "A record archive cannot be deleted.",
			},
		)
		self.assertTrue(frappe.db.exists("Audit Event", event))
		self.assertTrue(frappe.db.exists("Audit Anchor", anchor))
