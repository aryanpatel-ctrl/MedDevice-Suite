// The risk acceptability policy (module 39; plan D-13, D-21): QA writes it and signs it Reviewed;
// Management, a second person, signs it Approved and it is in force. Every rule is checked on the
// server (qms/risk_management.py).
const esign = frappe.provide("meddevice_suite.esign");
const MANAGEMENT = ["Manufacturing Manager", "Sales Manager", "Purchase Manager", "Stock Manager"];

frappe.ui.form.on("Risk Acceptability Policy", {
	refresh(frm) {
		const { docstatus, status, reviewed_by, in_force_since, superseded_by } = frm.doc;
		if (status === "In Force") {
			frm.dashboard.set_headline_alert(
				__("In force since {0}.", [frappe.datetime.str_to_user(in_force_since)]),
				"green"
			);
		} else if (status === "Superseded") {
			frm.dashboard.set_headline_alert(__("Superseded by {0}.", [superseded_by]), "red");
		}
		if (frm.is_new() || frm.is_dirty()) return; // sign what is saved, never unsaved edits

		const user = frappe.session.user;
		const qa = frappe.user.has_role("Quality Manager");
		const management = MANAGEMENT.some((role) => frappe.user.has_role(role));
		const offer = (label, action, meaning, options = {}) =>
			frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, options).catch(() => {})
			);
		if (docstatus !== 0) return;
		if (status === "Draft" && qa) {
			offer(__("Sign as Reviewed"), "Review", "Reviewed").addClass("btn-primary");
		}
		if (status === "Reviewed" && management && user !== reviewed_by) {
			offer(__("Approve"), "Approve", "Approved").addClass("btn-primary");
		}
		if (status === "Reviewed" && (qa || management)) {
			offer(__("Return for Changes"), "Return", "Rejected", { ask_reason: true });
		}
	},
});
