// QA's decision on a lot (modules 16 and 17): the form shows what QA needs to know about the
// lot and offers Release, Reject and Hold as signed acts, and Return to Stock and Reject Return
// for goods a customer sent back (plan D-25); the server checks every rule again
// (lot_control/release.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Lot Release", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus === 1 || frm.is_dirty()) {
			if (frm.doc.docstatus === 1) show_decision(frm);
			return;
		}
		const lot = frm.doc.__onload?.lot || {};
		const qa = frappe.user.has_role("Quality Manager");
		if (!qa) return;
		const sign = (label, action, meaning, primary) => {
			const button = frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, { ask_reason: true }).catch(() => {})
			);
			if (primary) button.removeClass("btn-default").addClass("btn-primary");
		};
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
	};
	frm.dashboard.set_headline_alert(
		__("{0}: {1}", [__(frm.doc.decision), frappe.utils.escape_html(frm.doc.reason || "")]),
		colours[frm.doc.decision] || "blue"
	);
}
