// A risk review (module 39), raised by the system. QA answers it with a new revision of the risk
// assessment, which closes it when approved, or closes it with a signed rationale that nothing
// changes. Every rule is checked on the server (qms/risk_management.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Risk Review", {
	refresh(frm) {
		if (frm.is_new() || frm.is_dirty()) return;
		if (frm.doc.docstatus !== 0 || frm.doc.status !== "Open") return;
		if (!frappe.user.has_role("Quality Manager")) return;
		frm.add_custom_button(__("Start New Revision"), () =>
			frappe
				.xcall("meddevice_suite.qms.risk_management.start_revision", {
					risk_management_file: frm.doc.risk_management_file,
					risk_review: frm.doc.name,
				})
				.then((name) => frappe.set_route("Form", "Risk Assessment", name))
				// Frappe has shown the server's refusal; the review stays as it was.
				.catch(() => {})
		);
		frm.add_custom_button(__("Close: No Change"), () =>
			esign.sign(frm, "Close: No Change", "Reviewed", { ask_reason: true }).catch(() => {})
		);
	},
});
