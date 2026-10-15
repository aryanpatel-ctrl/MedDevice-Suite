"""The data the browser test sheet of D10 Equipment Master starts from (qa/sheets/d10.py), proven on
its own.

The sheet's Cypress spec (cypress/integration/sheet_d10.js) calls these set-ups as the Administrator
of a test site, after ui_journey.prepare_journey, and its tests start from what they make. These
tests make the same calls the same way and check that every record is there, in the state the sheet
starts from, made by the job that makes it with its signatures; that the instrument an
out-of-tolerance calibration is about lists what it measured where it was; and that every set-up is
refused off a test site, to anyone but the Administrator, and without the run's password where it
signs.
"""

from unittest.mock import patch

import frappe
from frappe.utils import getdate, today

from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.master_data import equipment, item_master
from meddevice_suite.master_data.versioning import CHANGE_REQUEST
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d10
from meddevice_suite.qa.test_sheets import SheetTestCase, signed
from meddevice_suite.qa.ui_journey import COMPANY, PRODUCT, prepare_journey
from meddevice_suite.qa.ui_users import email_for, prepare_job_users
from meddevice_suite.qms import calibration
from meddevice_suite.qms.equipment_care import unfit
from meddevice_suite.setup.fixtures.inspection import INSTRUMENT_ITEM, INSTRUMENT_NAME, LABORATORY

QA, QC, MAINTENANCE = "QA Manager", "QC Inspector", "Maintenance"


def set_ups(password: str) -> list[tuple]:
	"""Every set-up the sheet offers the browser, with arguments of the kind it sends."""
	return [
		(d10.prepare_sheet, {"password": password}),
		(d10.prepare_instrument, {"password": password, "stage": "ready"}),
		(d10.prepare_machine, {"password": password, "needs": "maintenance"}),
		(d10.prepare_change, {"password": password, "asset": "Any asset"}),
		(d10.prepare_inspection, {"password": password}),
		(d10.prepare_measured_instrument, {"password": password}),
		(d10.run_impact_reminders, {}),
		(d10.signatures_bind, {"doctype": "Asset", "name": "Any asset"}),
		(d10.unique_tag, {}),
	]


@verifies("URS-PLT-007")
class TestSheetD10SetUpRefusals(SheetTestCase):
	def test_every_set_up_the_sheet_offers_is_checked_here(self):
		offered = {
			value
			for value in vars(d10).values()
			if callable(value)
			and getattr(value, "__module__", None) == d10.__name__
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
			(d10.prepare_sheet, {}),
			(d10.prepare_instrument, {"stage": "approved"}),
			(d10.prepare_machine, {"needs": "maintenance"}),
			(d10.prepare_change, {"asset": "Any asset"}),
			(d10.prepare_inspection, {}),
			(d10.prepare_measured_instrument, {}),
		]
		made = ("Asset", "Change Request", "Purchase Receipt", "Workstation", "Asset Category")
		before = {doctype: frappe.db.count(doctype) for doctype in made}
		for set_up, arguments in without:
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.ValidationError):
				set_up(password="", **arguments)
		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in made}, before)  # refused first

	def test_refuses_what_the_sheet_does_not_prepare(self):
		assets = frappe.db.count("Asset")
		for set_up, arguments in (
			(d10.prepare_instrument, {"stage": "retired"}),
			(d10.prepare_machine, {"needs": "calibration"}),
			(d10.prepare_machine, {"needs": "maintenance", "stage": "qualified"}),
			(d10.prepare_machine, {"needs": "qualification", "stage": "maintained"}),
			(d10.prepare_change, {"asset": "TEST-D10 No such asset"}),
		):
			with self.subTest(set_up=set_up.__name__, arguments=arguments):
				with self.assertRaises(frappe.ValidationError):
					set_up(password=self.password, **arguments)
		self.assertEqual(frappe.db.count("Asset"), assets)
		with self.assertRaises(frappe.ValidationError):
			d10.signatures_bind("Customer", "Any customer")  # not one of the sheet's records


@verifies("URS-PLT-007")
class TestSheetD10EquipmentMaster(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d10.prepare_sheet(password)

		cls.sheet = cls.prepared(before)

	def assertSignedBy(self, doctype: str, name: str, expected: list[tuple[str, str]]) -> None:
		self.assertEqual(signed(doctype, name), [(act, email_for(job)) for act, job in expected])

	def assertCalibrated(self, name: str, asset: str) -> None:
		record = frappe.get_doc("Calibration Record", name)
		self.assertEqual(
			(record.asset, record.docstatus, record.result, record.owner, record.completed_by),
			(asset, 1, calibration.PASS, email_for(MAINTENANCE), email_for(MAINTENANCE)),
		)
		self.assertSignedBy("Calibration Record", name, [("Complete", MAINTENANCE)])

	def test_names_what_the_sheet_works_with(self):
		sheet = self.sheet

		self.assertEqual(
			(sheet["company"], sheet["instrument_item"], sheet["machine_item"], sheet["laboratory"]),
			(COMPANY, INSTRUMENT_ITEM, d10.MACHINE_ITEM, LABORATORY),
		)
		self.assertEqual(
			sheet["journey_instrument"],
			frappe.db.get_value("Asset", {"asset_name": INSTRUMENT_NAME, "company": COMPANY}),
		)
		self.assertNotIn(sheet["journey_instrument"], unfit([sheet["journey_instrument"]]))
		self.assertTrue(frappe.db.exists("Supplier", LABORATORY))
		self.assertEqual(
			frappe.db.get_value("Asset Maintenance Team", sheet["maintenance_team"], "company"), COMPANY
		)

	def test_the_sheets_machine_item_an_equipment_item_qa_approved(self):
		item = frappe.get_doc("Item", d10.MACHINE_ITEM)

		self.assertEqual(
			(item.is_fixed_asset, item.is_stock_item, item.asset_category, item.get(item_master.ITEM_TYPE)),
			(1, 0, d10.MACHINE_CATEGORY, item_master.EQUIPMENT),
		)
		self.assertTrue(item_master.approved(d10.MACHINE_ITEM))
		self.assertIn(("Approve", email_for(QA)), signed("Item", d10.MACHINE_ITEM))

	def test_an_effective_sop_as_protocol_and_an_effective_work_instruction(self):
		for name, document_type in (
			(self.sheet["protocol"], "SOP"),
			(self.sheet["work_instruction"], "Work Instruction"),
		):
			with self.subTest(document_type=document_type):
				self.assertEqual(
					frappe.db.get_value("Controlled Document", name, ["document_type", "status"]),
					(document_type, "Effective"),
				)

	def test_an_instrument_ready_for_qas_approval(self):
		made = d10.prepare_instrument(self.password, "ready")

		doc = frappe.get_doc("Asset", made["asset"])
		self.assertEqual(
			(doc.get(equipment.APPROVAL_STATUS), doc.owner, doc.docstatus),
			(equipment.DRAFT, email_for(QA), 0),
		)
		self.assertEqual(equipment.missing_for_approval(doc), [])
		self.assertEqual((made["tag"], made["serial"]), (doc.get(equipment.TAG), doc.get(equipment.SERIAL)))
		self.assertEqual(signed("Asset", made["asset"]), [])

	def test_an_instrument_approved_by_qa_with_a_signature_not_calibrated(self):
		made = d10.prepare_instrument(self.password, "approved")

		self.assertTrue(equipment.approved(made["asset"]))
		self.assertSignedBy("Asset", made["asset"], [("Approve", QA)])
		self.assertIn("it has had no calibration", unfit([made["asset"]])[made["asset"]])
		self.assertNotIn("calibration", made)

	def test_an_instrument_calibrated_by_maintenance_with_a_signature(self):
		made = d10.prepare_instrument(self.password, "calibrated")

		self.assertCalibrated(made["calibration"], made["asset"])
		self.assertNotIn(made["asset"], unfit([made["asset"]]))

	def test_an_instrument_with_a_calibration_found_out_of_tolerance_drafted(self):
		made = d10.prepare_instrument(self.password, "out_of_tolerance")

		self.assertCalibrated(made["calibration"], made["asset"])
		draft = frappe.get_doc("Calibration Record", made["draft"])
		self.assertEqual(
			(draft.docstatus, draft.owner, draft.asset), (0, email_for(MAINTENANCE), made["asset"])
		)
		self.assertEqual((draft.out_of_tolerance, draft.result), (1, calibration.PASS))
		self.assertEqual(signed("Calibration Record", made["draft"]), [])

	def test_a_machine_needing_maintenance_approved_never_maintained(self):
		made = d10.prepare_machine(self.password, "maintenance")

		doc = frappe.get_doc("Asset", made["asset"])
		self.assertEqual(
			(
				doc.item_code,
				doc.get(equipment.CLASS),
				doc.get(equipment.MAINTENANCE),
				doc.get(equipment.MAINTENANCE_DAYS),
				doc.get(equipment.QUALIFICATION),
				doc.get(equipment.CALIBRATION),
				doc.get(equipment.WORKSTATION),
			),
			(d10.MACHINE_ITEM, "Production machine", 1, 30, 0, 0, None),
		)
		self.assertSignedBy("Asset", made["asset"], [("Approve", QA)])
		self.assertEqual(unfit([made["asset"]])[made["asset"]], ["it has had no preventive maintenance"])

	def test_a_machine_maintained_by_maintenance_with_a_signature(self):
		made = d10.prepare_machine(self.password, "maintenance", "maintained")

		record = frappe.get_doc("Maintenance Record", made["maintenance"])
		self.assertEqual(
			(record.asset, record.docstatus, record.result, record.completed_by),
			(made["asset"], 1, "Satisfactory", email_for(MAINTENANCE)),
		)
		self.assertSignedBy("Maintenance Record", record.name, [("Complete", MAINTENANCE)])
		self.assertNotIn(made["asset"], unfit([made["asset"]]))

	def test_a_machine_with_its_iq_executed_by_maintenance_and_approved_by_qa(self):
		made = d10.prepare_machine(self.password, "qualification", "iq_approved")

		iq = frappe.get_doc("Equipment Qualification", made["qualification"])
		self.assertEqual(
			(iq.asset, iq.stage, iq.docstatus, iq.outcome, iq.protocol, iq.executed_by, iq.approved_by),
			(made["asset"], "IQ", 1, "Pass", self.sheet["protocol"], email_for(MAINTENANCE), email_for(QA)),
		)
		self.assertSignedBy("Equipment Qualification", iq.name, [("Execute", MAINTENANCE), ("Approve", QA)])
		self.assertEqual(unfit([made["asset"]])[made["asset"]], ["it is not qualified (IQ, OQ and PQ)"])

	@verifies("URS-EQP-013")
	def test_a_machine_qualified_two_days_ago_at_a_workstation_and_moved_today_is_not_fit(self):
		made = d10.prepare_machine(self.password, "qualification", "qualified", at_workstation=1)

		asset = made["asset"]
		stations = made["workstations"]
		self.assertEqual(frappe.db.get_value("Asset", asset, equipment.WORKSTATION), stations["Line A"])
		self.assertEqual(set(made["qualification"]), {"IQ", "OQ", "PQ"})
		self.assertNotIn(asset, unfit([asset]))  # recorded at its first workstation: not a move

		change = d10.prepare_change(self.password, asset)
		self.assertApprovedChange(change, MAINTENANCE, "Asset", asset)
		with d10.act_as(QA):
			doc = frappe.get_doc("Asset", asset)
			doc.set(equipment.WORKSTATION, stations["Line B"])
			doc.set(CHANGE_REQUEST, change)
			doc.save()

		day = frappe.format_value(today(), {"fieldtype": "Date"})
		self.assertEqual(
			unfit([asset])[asset],
			[f"it was moved on {day} and has not been qualified again since (IQ, OQ, PQ)"],
		)

	def test_a_draft_inspection_by_qc_reading_with_the_journeys_caliper(self):
		made = d10.prepare_inspection(self.password)

		inspection = frappe.get_doc("Quality Inspection", made["inspection"])
		self.assertEqual(
			(inspection.docstatus, inspection.owner, inspection.batch_no, inspection.item_code),
			(0, email_for(QC), made["lot"], made["item"]),
		)
		self.assertEqual(
			[row.mds_instrument for row in inspection.readings if row.mds_instrument],
			[self.sheet["journey_instrument"]],
		)

	@verifies("URS-EQP-003")
	def test_an_instrument_moved_lists_what_it_measured_where_it_was(self):
		made = d10.prepare_measured_instrument(self.password)

		asset, stations, cards = made["asset"], made["workstations"], made["job_cards"]
		self.assertEqual(frappe.db.get_value("Asset", asset, equipment.WORKSTATION), stations["Now At"])
		self.assertEqual(frappe.db.get_value("Asset", asset, "mds_changed_under"), made["change_request"])
		self.assertCalibrated(made["calibration"], asset)
		self.assertEqual(
			frappe.db.get_value("Quality Inspection", made["inspection"], ["docstatus", "status", "owner"]),
			(1, "Accepted", email_for(QC)),
		)
		for card in cards.values():
			self.assertEqual(
				frappe.db.get_value("Job Card", card, ["docstatus", "posting_date", "production_item"]),
				(1, getdate(today()), PRODUCT),
			)
		listed = {
			row["reference_name"]: row.get("workstation")
			for row in calibration.impact_since(asset, today(), today())
		}
		self.assertEqual(
			listed,
			{
				made["inspection"]: None,
				cards["Was At"]: stations["Was At"],
				cards["Now At"]: stations["Now At"],
			},
		)
		draft = frappe.get_doc("Calibration Record", made["draft"])
		self.assertEqual((draft.docstatus, draft.out_of_tolerance), (0, 1))

	@verifies("URS-EQP-003", "URS-CMP-004")
	def test_the_completion_lists_the_impact_signs_it_and_the_reminder_follows_qas_assessment(self):
		made = d10.prepare_measured_instrument(self.password)
		name = made["draft"]

		with d10.act_as(MAINTENANCE):
			sign_action("Calibration Record", name, "Complete", self.password)
		record = frappe.get_doc("Calibration Record", name)
		self.assertEqual(
			(record.impact_status, getdate(record.last_good_calibration)), ("Open", getdate(today()))
		)
		self.assertEqual(
			{row.reference_name for row in record.impact},
			{made["inspection"], made["job_cards"]["Was At"], made["job_cards"]["Now At"]},
		)
		bound = d10.signatures_bind("Calibration Record", name)
		self.assertTrue(bound["chain"])
		self.assertEqual([(s["action"], s["unchanged"]) for s in bound["signatures"]], [("Complete", True)])

		d10.run_impact_reminders()
		reminder = {"category": "Calibration Impact", "reference_name": name}
		self.assertEqual(frappe.db.get_value("Reminder", reminder, "status"), "Open")
		with d10.act_as(QA):
			sign_action("Calibration Record", name, "Assess: No Product Impact", self.password, "Re-checked")
		d10.run_impact_reminders()
		self.assertEqual(frappe.db.get_value("Reminder", reminder, "status"), "Completed")
		bound = d10.signatures_bind("Calibration Record", name)
		self.assertEqual(
			[(s["action"], s["unchanged"]) for s in bound["signatures"]],
			[("Complete", True), ("Assess: No Product Impact", True)],
		)

	def test_an_instrument_without_a_draft_for_the_history_card(self):
		made = d10.prepare_measured_instrument(self.password, with_draft=0)

		self.assertNotIn("draft", made)
		self.assertEqual(frappe.db.count("Calibration Record", {"asset": made["asset"], "docstatus": 0}), 0)

	def test_a_tag_of_its_own_for_each_test(self):
		first, second = d10.unique_tag(), d10.unique_tag()

		self.assertEqual((len(first), len(second)), (10, 10))
		self.assertNotEqual(first, second)
