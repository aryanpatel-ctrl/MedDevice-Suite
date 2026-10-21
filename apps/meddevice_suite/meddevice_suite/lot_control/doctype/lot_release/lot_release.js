// QA's decision on a lot (modules 16 and 17): the form shows what QA needs to know about the
// lot and offers Release, Reject and Hold as signed acts, and Return to Stock and Reject Return
// for goods a customer sent back (plan D-25); a held lot may instead be released by concession,
// approved by QA with its justification and then by a second person from Management (plan D-35).
// The server checks every rule again (lot_control/release.py).
const esign = frappe.provide("meddevice_suite.esign");

const CONCESSION = "Release by Concession";

frappe.ui.form.on("Lot Release", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus === 1 || frm.is_dirty()) {
			if (frm.doc.docstatus === 1) show_decision(frm);
			return;
		}
		const lot = frm.doc.__onload?.lot || {};
		const sign = (label, action, meaning, primary, fields = []) => {
			const button = frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, { ask_reason: true, fields }).catch(() => {})
			);
			if (primary) button.removeClass("btn-default").addClass("btn-primary");
		};
		if (frm.doc.decision === CONCESSION) {
			// QA has approved the concession; only Management's approval is left (plan D-35).
			frm.dashboard.set_headline_alert(
				__(
					"Release by concession approved by QA ({0}): {1}. It waits for Management's approval.",
					[
						frappe.utils.escape_html(frm.doc.decided_by || ""),
						frappe.utils.escape_html(frm.doc.concession_justification || ""),
					]
				),
				"orange"
			);
			if (lot.approves_concession) {
				sign(__("Approve Concession"), "Approve Concession", "Approved", true);
			}
			return;
		}
		if (!frappe.user.has_role("Quality Manager")) return;
		if (lot.from_supplier && !lot.certificate_verified) {
			frm.dashboard.set_headline_alert(
				__(
					"The supplier certificate of this lot is not verified yet; QC verifies it on the receipt."
				),
				"orange"
			);
		}
		if (["Quarantine", "Hold"].includes(lot.status)) {
			sign(__("Release"), "Release", "Released", true);
			sign(__("Reject"), "Reject", "Rejected", false);
		}
		if (lot.status === "Hold") {
			sign(__("Release by Concession"), CONCESSION, "Approved", false, [
				{
					fieldtype: "Small Text",
					fieldname: "justification",
					label: __("Justification for the concession"),
					description: __(
						"Why the lot is released without the inspection its hold requires. Management approves it next."
					),
					reqd: 1,
				},
			]);
		}
		if (["Quarantine", "Approved"].includes(lot.status)) {
			sign(__("Hold"), "Hold", "Held", false);
		}
		if (lot.returned > 0) {
			frm.dashboard.add_comment(
				__("{0} of this lot came back from customers and waits in the Returns store.", [
					lot.returned,
				]),
				"blue",
				true
			);
			if (lot.status === "Approved") {
				sign(__("Return to Stock"), "Return to Stock", "Released", false);
			}
			sign(__("Reject Return"), "Reject Return", "Rejected", false);
		}
	},
});

function show_decision(frm) {
	const colours = {
		Release: "green",
		Reject: "red",
		Hold: "orange",
		"Return to Stock": "green",
		"Reject Return": "red",
		[CONCESSION]: "green",
	};
	frm.dashboard.set_headline_alert(
		__("{0}: {1}", [__(frm.doc.decision), frappe.utils.escape_html(frm.doc.reason || "")]),
		colours[frm.doc.decision] || "blue"
	);
}
