// QA's risk management report (module 39): its author signs it, and QA, another person, approves
// it. Every rule is checked on the server (qms/risk_management.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Risk Management Report", {
	setup(frm) {
		frm.set_query("risk_assessment", () => ({ filters: { status: "Approved" } }));
		frm.set_query("controlled_document", "information_sources", () => ({
			filters: { status: "Effective", document_type: "SOP" },
		}));
	},

	refresh(frm) {
		if (frm.is_new() || frm.is_dirty()) return; // sign what is saved, never unsaved edits
		const user = frappe.session.user;
		const qa = frappe.user.has_role("Quality Manager");
		const { docstatus, status, author } = frm.doc;
		if (status === "Superseded") {
			frm.dashboard.set_headline_alert(
				__("No longer the report of the risk assessment in force."),
				"red"
			);
		}
		if (docstatus !== 0) return;
		const offer = (label, action, meaning, options = {}) =>
			frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, options).catch(() => {})
			);
		if (status === "Draft" && user === author) {
			offer(__("Sign as Author"), "Author", "Authored").addClass("btn-primary");
		}
		if (status === "Awaiting Approval" && qa && user !== author) {
			offer(__("Approve"), "Approve", "Approved").addClass("btn-primary");
		}
		if (status === "Awaiting Approval" && qa) {
			offer(__("Return for Changes"), "Return", "Rejected", { ask_reason: true });
		}
	},
});
