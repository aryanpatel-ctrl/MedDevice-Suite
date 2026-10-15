"""Demo risk management (module 39), for the pilot site and the browser journey's test site.

Every finished good is listed in a risk management file whose risk management report is in force
before its device master record is approved (plan D-23, decided 2026-10-12), so each demo product
family gets one (plan D-13: one file per product family), made as the system makes it:

- the risk acceptability policy, when none is in force: QA writes it and signs it Reviewed, and
  Management, another person, approves it (plan D-21);
- the family's file, with its effective risk management plan, listing the family's products;
- a risk assessment authored by QA, reviewed by the Production Supervisor and approved by a second
  QA person: three people, each with a signature;
- the risk management report, authored by QA and approved by the second QA person.

The caller makes the second QA person's user (`SECOND_QA`, acted as and named like a job, with the
QA Manager's job). Everything is made only if missing; a policy, revision or report left part-way
by an earlier run is taken on from where it stopped, or withdrawn by QA if someone else wrote it.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager

import frappe
from frappe import _

from meddevice_suite.qms import risk_management as rm
from meddevice_suite.setup.fixtures.documents import ensure_work_instruction

QA, REVIEWER, MANAGEMENT = "QA Manager", "Production Supervisor", "Management"
# The second QA person, who approves what the first one wrote: QA does not approve its own
# assessment or report (module 39).
SECOND_QA = "Second QA"


def acceptability(severity: int, probability: int) -> str:
	"""The demo's matrix on the D-13 scales: severity counts double, so the matrix is not symmetric
	and a swapped axis shows. The client's own policy replaces it."""
	weight = 2 * severity + probability
	if weight >= 12:
		return rm.UNACCEPTABLE
	return rm.ALARP if weight >= 8 else rm.ACCEPTABLE


def ensure_policy(act_as: Callable[[str], AbstractContextManager], password: str, tag: str) -> str:
	"""The policy in force; when there is none, QA writes the demo's and signs it Reviewed, and
	Management approves it."""
	if current := rm.current_policy():
		return current
	title = f"Risk acceptability policy ({tag})"
	name = frappe.db.get_value(
		rm.POLICY, {"title": title, "docstatus": 0, "status": ["in", (rm.DRAFT, rm.REVIEWED)]}
	)
	if not name or frappe.db.get_value(rm.POLICY, name, "status") == rm.DRAFT:
		with act_as(QA):
			policy = (
				frappe.get_doc(rm.POLICY, name)
				if name
				else frappe.get_doc(
					{
						"doctype": rm.POLICY,
						"title": title,
						"policy_statement": f"ISO 14971 Annex C and the state of the art ({tag}).",
					}
				).insert()
			)
			for cell in policy.matrix:
				cell.acceptability = acceptability(cell.severity, cell.probability)
			policy.save()
			rm.review_policy(policy, password=password, reason=f"Scales and matrix checked ({tag})")
			name = policy.name
	with act_as(MANAGEMENT):
		rm.approve_policy(frappe.get_doc(rm.POLICY, name), password=password, reason=f"Approved ({tag})")
	return name


def ensure_risk_reports(
	families: dict[str, list[str]],
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
	tag: str = "demo",
) -> dict[str, str]:
	"""Each family's report in force, its file listing the family's products; returns family ->
	report."""
	return {
		family: ensure_risk_report(family, items, act_as, user_of, password, tag)
		for family, items in families.items()
	}


def ensure_risk_report(
	family: str,
	items: list[str],
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
	tag: str = "demo",
) -> str:
	"""The report in force of the family's file, which lists `items`."""
	ensure_policy(act_as, password, tag)
	plan = ensure_work_instruction(f"Risk management plan ({tag})", act_as, user_of, password, "SOP")
	sources = ensure_work_instruction(
		f"Post-market surveillance and complaints ({tag})", act_as, user_of, password, "SOP"
	)
	specification = ensure_work_instruction(
		f"Design specification: {family}", act_as, user_of, password, "Specification"
	)
	file = ensure_file(family, items, plan, act_as, tag)
	assessment = ensure_assessment(file, specification, act_as, user_of, password, tag)
	return ensure_report(file, assessment, sources, act_as, user_of, password, tag)


def ensure_file(
	family: str, items: list[str], plan: str, act_as: Callable[[str], AbstractContextManager], tag: str
) -> str:
	"""The family's file, listing `items`. Once it has an approved assessment, a product is added
	only through its next revision, under an approved change request: refused here, plainly."""
	file = frappe.db.get_value(rm.FILE, {"family_name": family})
	if not file:
		with act_as(QA):
			return (
				frappe.get_doc(
					{
						"doctype": rm.FILE,
						"family_name": family,
						"intended_use": f"As stated in the instructions for use of the family's devices ({tag}).",
						"foreseeable_misuse": f"Reuse of a single-use device; use after its expiry date ({tag}).",
						"risk_management_plan": plan,
						"items": [{"item": item} for item in items],
					}
				)
				.insert()
				.name
			)
	listed = set(frappe.get_all(rm.FILE_ITEM, filters={"parenttype": rm.FILE, "parent": file}, pluck="item"))
	missing = [item for item in items if item not in listed]
	if missing:
		if frappe.db.get_value(rm.FILE, file, "current_revision"):
			frappe.throw(
				_(
					"{0} has an approved risk assessment that does not list {1}; add them through its next revision, under an approved change request."
				).format(file, ", ".join(missing))
			)
		with act_as(QA):
			doc = frappe.get_doc(rm.FILE, file)
			for item in missing:
				doc.append("items", {"item": item})
			doc.save()
	return file


def ensure_assessment(
	file: str,
	specification: str,
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
	tag: str,
) -> str:
	"""The file's approved assessment: authored by QA, reviewed by the Production Supervisor and
	approved by the second QA person, each signing from where an earlier run left it."""
	if current := frappe.db.get_value(rm.FILE, file, "current_revision"):
		return current
	name = rm.revision_in_progress(file)
	if name and frappe.db.get_value(rm.ASSESSMENT, name, "author") != user_of(QA):
		with act_as(QA):  # left by someone else: QA withdraws it, with the reason
			rm.withdraw(
				frappe.get_doc(rm.ASSESSMENT, name),
				password=password,
				reason=f"Left by an earlier run ({tag})",
			)
		name = None
	if not name:
		with act_as(QA):
			name = (
				frappe.get_doc(
					{
						"doctype": rm.ASSESSMENT,
						"risk_management_file": file,
						"reviewer": user_of(REVIEWER),
						"hazards": [
							{
								"hazard_id": "H-01",
								"hazard_category": "Energy",
								"hazard": "Device fails in use",
								"sequence_of_events": f"Material or dimension outside the specification ({tag})",
								"hazardous_situation": "Fragments or leakage in the patient",
								"harm": "Infection; a procedure to retrieve the fragments",
								"severity": 4,
								"probability": 4,
								"residual_severity": 4,
								"residual_probability": 1,
								"justification": f"Every lot tested to the specification; as low as reasonably practicable ({tag}).",
							}
						],
						"controls": [
							{
								"hazard_id": "H-01",
								"option": rm.INHERENT,
								"description": "Materials, dimensions and performance specified and tested on every lot",
								"control_doctype": "Controlled Document",
								"control_name": specification,
								"implementation_evidence_type": "Controlled Document",
								"implementation_evidence": specification,
								"effectiveness_evidence_type": "Controlled Document",
								"effectiveness_evidence": specification,
							}
						],
					}
				)
				.insert()
				.name
			)
	steps = (
		(rm.DRAFT, QA, rm.author),
		(rm.IN_REVIEW, REVIEWER, rm.review),
		(rm.AWAITING_APPROVAL, SECOND_QA, rm.approve),
	)
	for status, job, act in steps:
		if frappe.db.get_value(rm.ASSESSMENT, name, "status") == status:
			with act_as(job):
				act(frappe.get_doc(rm.ASSESSMENT, name), password=password, reason=f"Signed ({tag})")
	return name


def ensure_report(
	file: str,
	assessment: str,
	sources: str,
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
	tag: str,
) -> str:
	"""The file's report in force: authored by QA and approved by the second QA person."""
	if current := frappe.db.get_value(rm.FILE, file, "current_report"):
		return current
	name = frappe.db.get_value(
		rm.REPORT,
		{"risk_assessment": assessment, "status": ["in", (rm.DRAFT, rm.AWAITING_APPROVAL)], "docstatus": 0},
	)
	if name and frappe.db.get_value(rm.REPORT, name, "author") != user_of(QA):
		with act_as(QA):  # left by someone else: QA withdraws it, with the reason
			rm.withdraw(
				frappe.get_doc(rm.REPORT, name), password=password, reason=f"Left by an earlier run ({tag})"
			)
		name = None
	if not name:
		with act_as(QA):
			name = (
				frappe.get_doc(
					{
						"doctype": rm.REPORT,
						"risk_assessment": assessment,
						"plan_implemented": 1,
						"plan_review": f"Each activity of the plan checked against its record ({tag}).",
						"overall_conclusion": f"The residual risk is as low as reasonably practicable and outweighed by the benefits of drainage ({tag}).",
						"information_sources": [{"controlled_document": sources}],
					}
				)
				.insert()
				.name
			)
	if frappe.db.get_value(rm.REPORT, name, "status") == rm.DRAFT:
		with act_as(QA):
			rm.author_report(frappe.get_doc(rm.REPORT, name), password=password, reason=f"Signed ({tag})")
	with act_as(SECOND_QA):
		rm.approve_report(frappe.get_doc(rm.REPORT, name), password=password, reason=f"Approved ({tag})")
	return name
