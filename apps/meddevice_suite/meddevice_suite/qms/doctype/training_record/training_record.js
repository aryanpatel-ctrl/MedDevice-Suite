// Training records (module 37): the trainee signs that they were trained (or read and understood
// a document their job allows reading for); the named assessor signs Competent or Not Competent;
// QA enters paper history and may revoke a competence. Every rule is checked on the server
// (qms/training.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Training Record", {
	setup(frm) {
		frm.set_query("document_revision", () => ({
			filters: { controlled_document: frm.doc.controlled_document, docstatus: 1 },
		}));
	},

	refresh(frm) {
		if (frm.is_new() || frm.is_dirty()) return; // sign what is saved, never unsaved edits
		const user = frappe.session.user;
		const qa = frappe.user.has_role("Quality Manager");
		const { docstatus, status, trigger } = frm.doc;
		const offer = (label, action, meaning, options = {}) =>
			frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, options).catch(() => {})
			);
		if (
			docstatus === 0 &&
			status === "Open" &&
			trigger !== "Historical" &&
			user === frm.doc.trainee_user
		) {
			const reading = frm.doc.training_method === "Read and Understand";
			offer(
				reading ? __("Sign: Read and Understood") : __("Sign: Trained"),
				"Trained",
				"Trained"
			).addClass("btn-primary");
		}
		if (docstatus === 0 && status === "Trained" && user === frm.doc.assessor) {
			offer(__("Competent"), "Competent", "Competent").addClass("btn-primary");
			offer(__("Not Competent"), "Not Competent", "Rejected", { ask_reason: true });
		}
		if (docstatus === 0 && trigger === "Historical" && qa) {
			offer(__("Record History"), "Record History", "Verified", {
				ask_reason: true,
			}).addClass("btn-primary");
		}
		if (docstatus === 1 && status === "Competent" && qa) {
			offer(__("Revoke Competence"), "Revoke", "Rejected", { ask_reason: true });
		}
	},
});
