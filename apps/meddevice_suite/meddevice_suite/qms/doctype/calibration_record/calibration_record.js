// Calibration (module 38): the performer completes the record with a signature; for one found
// out of tolerance, QA signs its assessment of what the instrument measured, also once the record
// is withdrawn for correction. QA corrects a completed record by signing Correct, which opens its
// amendment (plan D-29). Every rule is checked on the server (qms/calibration.py,
// qms/equipment_care.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Calibration Record", {
	refresh(frm) {
		if (frm.is_new() || frm.is_dirty()) return; // sign what is saved, never unsaved edits
		const qa = frappe.user.has_role("Quality Manager");
		if (frm.doc.docstatus === 0 && (qa || frappe.user.has_role("Maintenance User"))) {
			frm.add_custom_button(__("Complete"), () =>
				esign.sign(frm, "Complete", "Verified").catch(() => {})
			).addClass("btn-primary");
		}
		if (frm.doc.docstatus === 1 && qa) {
			frm.add_custom_button(__("Correct"), () =>
				esign
					.sign(frm, "Correct", "Rejected", { ask_reason: true })
					.then((amendment) => frappe.set_route("Form", frm.doctype, amendment))
					.catch(() => {})
			);
		}
		if (frm.doc.docstatus !== 0 && frm.doc.impact_status === "Open" && qa) {
			for (const act of ["Assess: No Product Impact", "Assess: Product Action Needed"]) {
				frm.add_custom_button(
					__(act),
					() => esign.sign(frm, act, "Reviewed", { ask_reason: true }).catch(() => {}),
					__("Assess Impact")
				);
			}
		}
	},
});
