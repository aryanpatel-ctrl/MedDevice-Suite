"""What the browser test sheet of D10 Equipment Master starts from (modules 8, 38, 40, 62;
URS-MST-010, URS-MST-011, URS-EQP-001 to URS-EQP-013), on a test site only
(cypress/integration/sheet_d10.js).

The sheet's tests act on the screens as QA, Maintenance, QC and Management: QA records and approves
equipment with a signature and changes it under change control, Maintenance completes calibrations
and preventive maintenance and executes IQ, OQ and PQ with signatures, QA assesses what an
instrument found out of tolerance measured, approves and returns qualifications, and corrects
completed records with its signed Correct; QC is refused an instrument that is not fit; Management
reads the history card and the care schedule.

The sheet names the pilot's Digital Caliper (Demo). Every inspection of the browser tests reads
with the journey's caliper (ui_journey.prepare_journey), and the sheet takes instruments out of
service, finds them out of tolerance and withdraws their calibrations, so each test is given
equipment of its own here instead, made afresh each run as the system makes it, each step by the
job that does it (`acting_as`) through the code's own services and signed acts:

- an instrument recorded by QA, approved by QA with a signature, calibrated by an external
  laboratory and completed by Maintenance with a signature, as far along as the test needs;
- a production machine of the sheet's own equipment item, needing preventive maintenance or
  qualification, maintained or qualified as far along as the test needs;
- an instrument whose readings and workstations an out-of-tolerance calibration lists: an
  inspection QC submitted with it, and the job cards at the workstation it was moved from under an
  approved change request, at the one it is at now and at one it never was at;
- an instrument whose calibration, done by an external laboratory 400 days ago, is overdue: a
  reference standard out of calibration;
- an instrument found out of tolerance, its calibration completed by Maintenance and its impact
  assessed by QA, each with a signature, and a draft inspection by QC for it to be named in after;
- an approved change request listing a piece of equipment, raised by Maintenance and assessed and
  approved by QA with signatures;
- a lot QC can inspect, with a draft inspection reading with the journey's caliper.

The job cards are stored as they stand, as the impact tests store them
(qms/test_equipment_care.py TestImpactFollowsTheInstrument): a job card is made from a work order on
the journey's routing, whose workstations the sheet does not put its instruments at; what is tested
here is where and when they were made, which is what the calibration lists. The rules at the job
card itself are the production sheet's.

The Calibration Impact reminders are raised and settled by the daily run; `run_impact_reminders`
runs that category of it, as the scheduler would, for the sheet to see them. `signatures_bind`
says whether the signatures on a record still bind exactly what is stored, which the desk's Verify
Integrity does not check (it checks the chain), for the signature over the impact rows the
completion itself adds.

Only the Administrator who runs the tests prepares the sheet.
"""

from contextlib import AbstractContextManager

import frappe
from frappe import _
from frappe.utils import add_days, cint, today

from meddevice_suite.compliance.esign import is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.master_data import equipment, item_master
from meddevice_suite.master_data.specification import INCOMING, specification_for
from meddevice_suite.master_data.versioning import CHANGE_REQUEST
from meddevice_suite.qa.sheets.d4 import MATERIAL, ensure_supplier_approved, received, verified
from meddevice_suite.qa.ui_journey import COMPANY, PRODUCT, RAW_MATERIALS, acting_as, approve_as_qa
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.qms import change_control
from meddevice_suite.setup.fixtures.documents import ensure_work_instruction
from meddevice_suite.setup.fixtures.inspection import (
	INSTRUMENT_EQUIPMENT,
	INSTRUMENT_ITEM,
	INSTRUMENT_NAME,
	LABORATORY,
	LOCATION,
	SAMPLE_SIZE,
	draft_calibration,
	readings_for,
)
from meddevice_suite.work_management import reminders

QA, QC, MAINTENANCE = "QA Manager", "QC Inspector", "Maintenance"
PRODUCTION = "Production Supervisor"
REASON = "Browser test sheet D10"
# The sheet's production machine: an equipment item of its own, in a category of its own.
MACHINE_ITEM = "UIJ-D10-MACHINE"
MACHINE_ITEM_NAME = "Catheter tipping machine (browser test)"
MACHINE_HSN = "84779000"  # machinery for working rubber or plastics
MACHINE_CATEGORY = "Production Machines (browser test)"
MAINTENANCE_TEAM = "UIJ D10 Maintenance Team"
# The protocols an IQ, OQ or PQ follows (an effective SOP), and an effective work instruction,
# which is not one.
PROTOCOL_TITLE = "Equipment qualification protocol (browser test D10)"
WORK_INSTRUCTION_TITLE = "Tipping machine set-up (browser test D10)"
# How far along an instrument is prepared (`prepare_instrument`).
INSTRUMENT_STAGES = ("ready", "approved", "calibrated", "out_of_tolerance", "overdue")
# How long ago an overdue instrument was calibrated: longer than its interval of 365 days.
OVERDUE_DAYS = 400
# QA's signed assessment of an out-of-tolerance finding (`prepare_assessed_finding`).
ASSESSMENT_REASON = f"{REASON}: nothing was measured with it since its last good calibration"
# What a machine needs and how far along it is prepared (`prepare_machine`).
MACHINE_NEEDS = ("maintenance", "qualification")
MACHINE_STAGES = ("approved", "maintained", "iq_approved", "qualified")
# What a machine needs for it to be taken to the stage.
MACHINE_STAGE_NEEDS = {
	"maintained": "maintenance",
	"iq_approved": "qualification",
	"qualified": "qualification",
}
# A calibration with every value found and left in tolerance, and one found out of tolerance.
FOUND_OUT_OF_TOLERANCE = 100.05
# The records whose signatures `signatures_bind` reads.
SIGNED_TYPES = ("Asset", "Calibration Record", "Maintenance Record", "Equipment Qualification")


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


def act_as(profile: str) -> AbstractContextManager:
	return acting_as(email_for(profile))


def require_password(password: str | None) -> str:
	if not password:
		frappe.throw(_("Give the run's password of the job users."))
	return password


def signed(profile: str, doctype: str, name: str, act: str, password: str, reason: str | None = None):
	"""The job signs the act through the signing service, as its dialog sends it."""
	with act_as(profile):
		return sign_action(doctype, name, act, password, reason)


def tag() -> str:
	return frappe.generate_hash(length=10).upper()


# --- The sheet's masters ------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""The sheet's equipment item, its laboratory, its protocols and its maintenance team, after the
	journey's masters (ui_journey.prepare_journey); and what the tests work with."""
	require_administrator()
	require_password(password)
	ensure_laboratory()
	ensure_machine_item(password)
	ensure_maintenance_team()
	protocol = ensure_document(PROTOCOL_TITLE, "SOP", password)
	work_instruction = ensure_document(WORK_INSTRUCTION_TITLE, "Work Instruction", password)
	return {
		"company": COMPANY,
		"instrument_item": INSTRUMENT_ITEM,
		"machine_item": MACHINE_ITEM,
		"location": LOCATION,
		"laboratory": LABORATORY,
		"protocol": protocol,
		"work_instruction": work_instruction,
		"maintenance_team": MAINTENANCE_TEAM,
		"journey_instrument": journey_instrument(),
	}


def journey_instrument() -> str:
	return frappe.db.get_value("Asset", {"asset_name": INSTRUMENT_NAME, "company": COMPANY})


def ensure_laboratory() -> None:
	"""The external calibration laboratory, a supplier, as the instrument's calibration names it."""
	if not frappe.db.exists("Supplier", LABORATORY):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": LABORATORY,
				"supplier_group": "Services"
				if frappe.db.exists("Supplier Group", "Services")
				else "All Supplier Groups",
			}
		).insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller


def ensure_machine_item(password: str) -> None:
	"""The sheet's production machine as an equipment item behind its assets (decided 2026-10-10),
	in an asset category of its own, approved by QA with a signature (module 1)."""
	if not frappe.db.exists("Asset Category", MACHINE_CATEGORY):

		def account(account_type: str) -> str | None:
			return frappe.db.get_value(
				"Account", {"company": COMPANY, "account_type": account_type, "is_group": 0}, "name"
			)

		frappe.get_doc(
			{
				"doctype": "Asset Category",
				"asset_category_name": MACHINE_CATEGORY,
				"accounts": [
					{
						"company_name": COMPANY,
						"fixed_asset_account": account("Fixed Asset"),
						"accumulated_depreciation_account": account("Accumulated Depreciation"),
						"depreciation_expense_account": account("Depreciation"),
					}
				],
			}
		).insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
	if not frappe.db.exists("Item", MACHINE_ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": MACHINE_ITEM,
				"item_name": MACHINE_ITEM_NAME,
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"gst_hsn_code": MACHINE_HSN,
				"is_stock_item": 0,
				"is_fixed_asset": 1,
				"asset_category": MACHINE_CATEGORY,
				item_master.ITEM_TYPE: item_master.EQUIPMENT,
			}
		).insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
	approve_as_qa(password)(MACHINE_ITEM)


def ensure_maintenance_team() -> None:
	"""A maintenance team of ERPNext's, which its asset maintenance plan names (D10-T17)."""
	if not frappe.db.exists("Asset Maintenance Team", MAINTENANCE_TEAM):
		frappe.get_doc(
			{
				"doctype": "Asset Maintenance Team",
				"maintenance_team_name": MAINTENANCE_TEAM,
				"company": COMPANY,
				"maintenance_team_members": [
					{"team_member": email_for(MAINTENANCE), "maintenance_role": "Maintenance User"}
				],
			}
		).insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller


def ensure_document(title: str, document_type: str, password: str) -> str:
	"""An effective controlled document of the type, issued through document control by the jobs
	with signatures (module 30)."""
	return ensure_work_instruction(
		title, lambda profile: acting_as(email_for(profile)), email_for, password, document_type
	)


# --- Equipment, through the jobs ----------------------------------------------------------------


def new_equipment(values: dict) -> str:
	"""A piece of equipment recorded by QA on ERPNext's asset, as the form saves it: in draft."""
	suffix = tag()
	with act_as(QA):
		return (
			frappe.get_doc(
				{
					"doctype": "Asset",
					"asset_name": f"Caliper {suffix} (browser test D10)",
					"item_code": INSTRUMENT_ITEM,
					"company": COMPANY,
					"location": LOCATION,
					"purchase_date": today(),
					"available_for_use_date": today(),
					"asset_type": "Existing Asset",  # bought before the system: no purchase document
					"net_purchase_amount": 15000,
					"asset_quantity": 1,
					"calculate_depreciation": 0,
					**INSTRUMENT_EQUIPMENT,
					equipment.TAG: f"QC-CAL-{suffix}",
					equipment.SERIAL: f"SN-{suffix}",
					**values,
				}
			)
			.insert()
			.name
		)


def approved(asset: str, password: str) -> str:
	signed(QA, "Asset", asset, "Approve", password, f"{REASON}: make, model and serial checked")
	return asset


def calibrated(asset: str, password: str, performed_on: str | None = None) -> str:
	"""An external laboratory's calibration in tolerance, done today or on `performed_on`, drafted
	and completed by Maintenance with a signature (setup.fixtures.inspection.draft_calibration)."""
	with act_as(MAINTENANCE):
		record = draft_calibration(asset)
		if performed_on:
			record.performed_on = performed_on
			record.save()
		name = record.name
	signed(MAINTENANCE, "Calibration Record", name, "Complete", password)
	return name


def drafted_out_of_tolerance(asset: str) -> str:
	"""A calibration of the instrument whose value was found out of tolerance and left in it after
	adjustment, drafted by Maintenance for its signature on the screen."""
	with act_as(MAINTENANCE):
		record = draft_calibration(asset)
		record.points[0].as_found = FOUND_OUT_OF_TOLERANCE
		record.save()
		return record.name


def asset_details(asset: str) -> dict:
	values = frappe.db.get_value(
		"Asset", asset, ["name", "asset_name", equipment.TAG, equipment.SERIAL, "mds_version"], as_dict=True
	)
	return {
		"asset": values.name,
		"asset_name": values.asset_name,
		"tag": values.get(equipment.TAG),
		"serial": values.get(equipment.SERIAL),
		"version": values.mds_version,
	}


@frappe.whitelist(methods=["POST"])
def prepare_instrument(password: str, stage: str) -> dict:
	"""A new measuring instrument of the sheet, needing calibration every 365 days, taken as far as
	`stage`: ready (recorded by QA with all approval needs, in draft), approved (by QA with a
	signature), calibrated (its calibration completed by Maintenance with a signature),
	out_of_tolerance (calibrated, then a second calibration found out of tolerance drafted by
	Maintenance) or overdue (calibrated OVERDUE_DAYS ago, so its calibration was due before today).
	Returns {asset, asset_name, tag, serial, version, calibration?, draft?}."""
	require_administrator()
	require_password(password)
	if stage not in INSTRUMENT_STAGES:
		frappe.throw(_("Unknown stage {0}.").format(stage))
	asset = new_equipment({})
	made = {}
	if stage != "ready":
		approved(asset, password)
	if stage in ("calibrated", "out_of_tolerance"):
		made["calibration"] = calibrated(asset, password)
	if stage == "out_of_tolerance":
		made["draft"] = drafted_out_of_tolerance(asset)
	if stage == "overdue":
		made["calibration"] = calibrated(asset, password, add_days(today(), -OVERDUE_DAYS))
	return {**asset_details(asset), **made}


@frappe.whitelist(methods=["POST"])
def prepare_assessed_finding(password: str, with_inspection: int = 1) -> dict:
	"""A new instrument of the sheet, approved and calibrated today, then found out of tolerance by a
	second calibration completed by Maintenance with a signature, which listed nothing it measured,
	and assessed by QA with a signature as having no product impact; with `with_inspection`, a draft
	incoming inspection by QC of a new lot reading with the journey's caliper, for QC to name this
	instrument in after the assessment. Returns {asset, asset_name, tag, serial, version,
	calibration, finding, assessment_reason, inspection?, lot?}."""
	require_administrator()
	require_password(password)
	asset = approved(new_equipment({}), password)
	made = {"calibration": calibrated(asset, password)}
	finding = drafted_out_of_tolerance(asset)
	signed(MAINTENANCE, "Calibration Record", finding, "Complete", password)
	signed(QA, "Calibration Record", finding, "Assess: No Product Impact", password, ASSESSMENT_REASON)
	made.update(finding=finding, assessment_reason=ASSESSMENT_REASON)
	if cint(with_inspection):
		lot = verified_lot(password)
		made.update(inspection=inspection_with(lot, journey_instrument(), submit=False), lot=lot["lot"])
	return {**asset_details(asset), **made}


def machine_values(needs: str, workstation: str | None = None) -> dict:
	"""A production machine of the sheet's item that needs `needs`, at the workstation if given."""
	suffix = tag()
	care = (
		{equipment.MAINTENANCE: 1, equipment.MAINTENANCE_DAYS: 30}
		if needs == "maintenance"
		else {equipment.QUALIFICATION: 1, equipment.REQUALIFICATION_DAYS: 365}
	)
	return {
		"asset_name": f"Tipping Machine {suffix} (browser test D10)",
		"item_code": MACHINE_ITEM,
		equipment.TAG: f"TM-{suffix}",
		equipment.CLASS: "Production machine",
		equipment.MAKER: "Demo Machines",
		equipment.MODEL: "TM-02",
		equipment.SERIAL: f"TM-SN-{suffix}",
		equipment.QUALITY_IMPACT: 1,
		equipment.CALIBRATION: 0,
		equipment.CALIBRATION_DAYS: 0,
		equipment.WORKSTATION: workstation,
		**care,
	}


def maintained(asset: str, password: str) -> str:
	"""Satisfactory preventive maintenance, recorded and completed by Maintenance with a signature."""
	with act_as(MAINTENANCE):
		name = (
			frappe.get_doc(
				{
					"doctype": "Maintenance Record",
					"asset": asset,
					"performed_on": today(),
					"checks": [
						{"check": "Lubrication", "outcome": "OK"},
						{"check": "Belt tension", "outcome": "OK"},
					],
					"actions_performed": "Greased guide rails; belt checked (browser test D10)",
					"downtime_hours": 1,
				}
			)
			.insert()
			.name
		)
	signed(MAINTENANCE, "Maintenance Record", name, "Complete", password)
	return name


def qualified_stage(asset: str, stage: str, performed_on: str, password: str) -> str:
	"""An IQ, OQ or PQ against the sheet's protocol, passed, executed by Maintenance and approved by
	QA, each with a signature."""
	with act_as(MAINTENANCE):
		name = (
			frappe.get_doc(
				{
					"doctype": "Equipment Qualification",
					"asset": asset,
					"stage": stage,
					"occasion": "Initial",
					"protocol": ensure_document(PROTOCOL_TITLE, "SOP", password),
					"performed_on": performed_on,
					"tests": [
						{
							"test": f"{stage}: as the protocol states",
							"acceptance_criteria": "As the protocol states",
							"observed": "As stated (browser test D10)",
							"outcome": "Pass",
						}
					],
				}
			)
			.insert()
			.name
		)
	signed(MAINTENANCE, "Equipment Qualification", name, "Execute", password)
	signed(QA, "Equipment Qualification", name, "Approve", password, f"{REASON}: {stage} complete")
	return name


def new_workstations(*labels: str) -> dict[str, str]:
	"""Workstations of the sheet's own, new each run, recorded by the Production Supervisor, by
	label: nothing is made at them but the sheet's job cards."""
	suffix = tag()
	with act_as(PRODUCTION):
		return {
			label: frappe.get_doc(
				{
					"doctype": "Workstation",
					"workstation_name": f"UIJ D10 {label} {suffix}",
					"production_capacity": 1,
				}
			)
			.insert()
			.name
			for label in labels
		}


@frappe.whitelist(methods=["POST"])
def prepare_machine(password: str, needs: str, stage: str = "approved", at_workstation: int = 0) -> dict:
	"""A new production machine of the sheet's item that needs `needs` (maintenance: every 30 days;
	qualification: again every 365 days), approved by QA with a signature, and taken as far as
	`stage`: approved, maintained (a satisfactory maintenance completed by Maintenance), iq_approved
	(an IQ executed by Maintenance and approved by QA) or qualified (IQ, OQ and PQ done two days ago,
	each executed and approved). With `at_workstation`, it is at a workstation of the sheet's own and
	another is ready to move it to. Returns {asset, asset_name, tag, serial, version, maintenance?,
	qualification?, workstations?}."""
	require_administrator()
	require_password(password)
	if needs not in MACHINE_NEEDS or stage not in MACHINE_STAGES:
		frappe.throw(_("Unknown machine {0} {1}.").format(needs, stage))
	if stage in MACHINE_STAGE_NEEDS and MACHINE_STAGE_NEEDS[stage] != needs:
		frappe.throw(_("A machine that needs {0} is not {1}.").format(needs, stage))
	made = {}
	workstation = None
	if cint(at_workstation):
		made["workstations"] = new_workstations("Line A", "Line B")
		workstation = made["workstations"]["Line A"]
	asset = approved(new_equipment(machine_values(needs, workstation)), password)
	if stage == "maintained":
		made["maintenance"] = maintained(asset, password)
	elif stage == "iq_approved":
		made["qualification"] = qualified_stage(asset, "IQ", today(), password)
	elif stage == "qualified":
		done_on = add_days(today(), -2)
		made["qualification"] = {
			stage_name: qualified_stage(asset, stage_name, done_on, password)
			for stage_name in ("IQ", "OQ", "PQ")
		}
	return {**asset_details(asset), **made}


# --- Change control -----------------------------------------------------------------------------


def approved_change(asset: str, password: str) -> str:
	"""A minor change request listing the equipment, raised by Maintenance and assessed and approved
	by QA with signatures (module 36)."""
	with act_as(MAINTENANCE):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Change {asset} (browser test D10)",
				"change_type": change_control.CHANGE_TYPE_OF["Asset"],
				"description": "Equipment record changed (browser test D10).",
				"reason": "Found on the equipment's label and the line layout (browser test D10).",
				"affected_records": [{"reference_doctype": "Asset", "reference_name": asset}],
				"actions": [
					{
						"description": f"Change {asset}",
						"action_owner": email_for(MAINTENANCE),
						"due_date": today(),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	with act_as(QA):
		request = frappe.get_doc("Change Request", request.name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_summary": "The equipment's record only (browser test D10).",
				"classification": "Minor",
				"effectiveness_check_days": 90,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason=f"{REASON}: assessed")
		change_control.approve(
			frappe.get_doc("Change Request", request.name),
			password=password,
			reason=f"{REASON}: approved",
		)
	return request.name


@frappe.whitelist(methods=["POST"])
def prepare_change(password: str, asset: str) -> str:
	"""An approved change request listing the equipment, for QA to name on its form."""
	require_administrator()
	require_password(password)
	if not frappe.db.exists("Asset", asset):
		frappe.throw(_("Equipment {0} does not exist.").format(asset))
	return approved_change(asset, password)


def moved(asset: str, workstation: str, password: str) -> str:
	"""QA moves the equipment to another workstation on its record under an approved change request,
	as the form saves it; the audit trail records the move. Returns the change request."""
	change = approved_change(asset, password)
	with act_as(QA):
		doc = frappe.get_doc("Asset", asset)
		doc.set(equipment.WORKSTATION, workstation)
		doc.set(CHANGE_REQUEST, change)
		doc.save()
	return change


# --- What an instrument measured ----------------------------------------------------------------


def inspection_with(lot: dict, instrument: str, submit: bool) -> str:
	"""QC's incoming inspection of the lot against the material's specification, its measurement
	read with `instrument`: submitted (accepted), or left in draft."""
	with act_as(QC):
		inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": "Purchase Receipt",
				"reference_name": lot["receipt"],
				"item_code": lot["item"],
				"batch_no": lot["lot"],
				"sample_size": SAMPLE_SIZE,
				"inspected_by": frappe.session.user,
				"readings": readings_for(specification_for(lot["item"], INCOMING), instrument),
			}
		).insert()
		if submit:
			inspection.submit()
		return inspection.name


def verified_lot(password: str) -> dict:
	"""A lot of the journey's material received by Stores into quarantine and its certificate
	verified by QC (qa/sheets/d4.py), from the supplier QA approved for it."""
	ensure_supplier_approved(password, [*RAW_MATERIALS])
	lot = received(MATERIAL, 10)
	verified(lot)
	return lot


@frappe.whitelist(methods=["POST"])
def prepare_inspection(password: str) -> dict:
	"""A draft incoming inspection by QC of a new lot, its measurement read with the journey's
	caliper, for QC to name another instrument on the screen. Returns {inspection, lot, item,
	instrument}."""
	require_administrator()
	require_password(password)
	lot = verified_lot(password)
	instrument = journey_instrument()
	return {
		"inspection": inspection_with(lot, instrument, submit=False),
		"lot": lot["lot"],
		"item": lot["item"],
		"instrument": instrument,
	}


def job_card_at(workstation: str) -> str:
	"""A submitted job card made at the workstation today, stored as it stands (see the module's
	docstring): the calibration lists it by its workstation and its day."""
	card = frappe.new_doc("Job Card")
	card.update(
		{
			"workstation": workstation,
			"production_item": PRODUCT,
			"posting_date": today(),
			"company": COMPANY,
			"for_quantity": 1,
			"docstatus": 1,
		}
	)
	card.set_new_name()
	card.set_user_and_timestamp()
	card.db_insert()
	return card.name


@frappe.whitelist(methods=["POST"])
def prepare_measured_instrument(password: str, with_draft: int = 1) -> dict:
	"""A new instrument recorded at a workstation of the sheet's own, approved and calibrated, with
	which QC then submitted an inspection; moved by QA to a second workstation under an approved
	change request; with a job card made today at the workstation it was moved from, one at the one it
	is at now and one at a workstation it never was at; and, with `with_draft`, a calibration found
	out of tolerance drafted by Maintenance. Returns {asset, asset_name, tag, serial, version,
	calibration, inspection, lot, change_request, workstations, job_cards, draft?}."""
	require_administrator()
	require_password(password)
	stations = new_workstations("Was At", "Now At", "Never At")
	asset = approved(new_equipment({equipment.WORKSTATION: stations["Was At"]}), password)
	calibration = calibrated(asset, password)
	lot = verified_lot(password)
	inspection = inspection_with(lot, asset, submit=True)
	cards = {"Was At": job_card_at(stations["Was At"])}
	change = moved(asset, stations["Now At"], password)
	cards["Now At"] = job_card_at(stations["Now At"])
	cards["Never At"] = job_card_at(stations["Never At"])
	made = {
		"calibration": calibration,
		"inspection": inspection,
		"lot": lot["lot"],
		"change_request": change,
		"workstations": stations,
		"job_cards": cards,
	}
	if cint(with_draft):
		made["draft"] = drafted_out_of_tolerance(asset)
	return {**asset_details(asset), **made}


# --- The daily run, and what the signatures bind -------------------------------------------------


@frappe.whitelist(methods=["POST"])
def run_impact_reminders() -> None:
	"""The daily run's Calibration Impact reminders, as the scheduler runs them: raised for each
	open out-of-tolerance assessment, completed once QA has signed it."""
	require_administrator()
	reminders.track("Calibration Impact", reminders.CATEGORIES["Calibration Impact"].due)


@frappe.whitelist(methods=["POST"])
def signatures_bind(doctype: str, name: str) -> dict:
	"""Whether each signature on the record still binds exactly what is stored (esign
	is_unchanged_since), and whether the record's chain of signatures is intact. Returns {chain,
	signatures: [{action, meaning, unchanged}]}."""
	require_administrator()
	if doctype not in SIGNED_TYPES:
		frappe.throw(_("{0} is not one of the sheet's records.").format(doctype))
	signatures = frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": doctype, "reference_name": name},
		pluck="name",
		order_by="creation asc",
	)
	return {
		"chain": verify_signatures(doctype, name),
		"signatures": [
			{
				"action": signature.action,
				"meaning": signature.meaning,
				"unchanged": is_unchanged_since(signature),
			}
			for signature in (frappe.get_doc("Electronic Signature", each) for each in signatures)
		],
	}


@frappe.whitelist(methods=["POST"])
def unique_tag() -> str:
	"""A tag that names the records a test makes on the screen apart from any earlier run's."""
	require_administrator()
	return tag()
