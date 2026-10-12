// A controlled document (module 30): QA registers it, its process owner or QA starts each new
// revision, and QA withdraws it with a signature. The server checks every rule again.
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Controlled Document", {
	refresh(frm) {
		if (frm.is_new()) return;
		const qa = frappe.user.has_role("Quality Manager");
		const owner = frappe.session.user === frm.doc.process_owner;
		const in_progress = frm.doc.__onload?.revision_in_progress;

		if (frm.doc.status === "Obsolete") {
			frm.dashboard.set_headline_alert(
				__("Withdrawn: {0}", [frappe.utils.escape_html(frm.doc.withdrawn_reason || "")]),
				"red"
			);
			return;
		}
		if (frm.doc.current_revision) {
			frm.add_custom_button(__("Effective Revision"), () =>
				frappe.set_route("Form", "Document Revision", frm.doc.current_revision)
			);
		}
		if (in_progress) {
			frm.add_custom_button(__("Revision in Progress"), () =>
				frappe.set_route("Form", "Document Revision", in_progress)
			);
		} else if (qa || owner) {
			frm.add_custom_button(__("New Revision"), () =>
				frappe.new_doc("Document Revision", { controlled_document: frm.doc.name })
			);
		}
		if (qa && !in_progress) {
			frm.add_custom_button(
				__("Withdraw"),
				() =>
					esign.sign(frm, "Withdraw", "Withdrawn", { ask_reason: true }).catch(() => {}),
				__("Actions")
			);
		}
	},
});
