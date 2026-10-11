// Equipment qualification (module 40): the performer executes it with a signature; QA, never
// the performer, approves it or returns it with a signed reason. Every rule is checked on the
// server (qms/qualification.py).
const esign = frappe.provide("meddevice_suite.esign");
const PERFORMERS = ["Maintenance User", "Manufacturing User", "Quality Manager"];

frappe.ui.form.on("Equipment Qualification", {
	refresh(frm) {
		if (frm.is_new() || frm.is_dirty() || frm.doc.docstatus !== 0) return;
		if (!frm.doc.executed_by && PERFORMERS.some((role) => frappe.user.has_role(role))) {
			frm.add_custom_button(__("Execute"), () =>
				esign.sign(frm, "Execute", "Verified").catch(() => {})
			).addClass("btn-primary");
		}
		const qa = frappe.user.has_role("Quality Manager");
		if (frm.doc.executed_by && qa && frm.doc.executed_by !== frappe.session.user) {
			frm.add_custom_button(__("Approve"), () =>
				esign.sign(frm, "Approve", "Approved", { ask_reason: true }).catch(() => {})
			).addClass("btn-primary");
			frm.add_custom_button(__("Return"), () =>
				esign.sign(frm, "Return", "Rejected", { ask_reason: true }).catch(() => {})
			);
		}
	},
});
