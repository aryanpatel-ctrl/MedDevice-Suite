// Training matrix (module 37): QA approves a job's training matrix with a signature; a change is
// a cancel and amend, approved again. Every rule is checked on the server (qms/training.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Training Matrix", {
	refresh(frm) {
		if (frm.is_new() || frm.is_dirty() || frm.doc.docstatus !== 0) return;
		if (frappe.user.has_role("Quality Manager")) {
			frm.add_custom_button(__("Approve"), () =>
				esign.sign(frm, "Approve", "Approved", { ask_reason: true }).catch(() => {})
			).addClass("btn-primary");
		}
	},
});
