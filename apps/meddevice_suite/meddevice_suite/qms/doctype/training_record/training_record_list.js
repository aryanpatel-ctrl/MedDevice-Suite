// Training records are opened by the system (module 37). The one record a person adds is QA's
// record of a training given on paper before the system, so the list's Add button (shown only to
// QA, who alone may create) starts that: QA names the employee and the document, the server opens
// a draft Historical record on the revision in effect, and QA completes it on the form with the
// certificate and the Record History signature. The rules are on the server
// (qms/training.py, start_history and before_insert).
frappe.listview_settings["Training Record"] = {
	primary_action() {
		const dialog = new frappe.ui.Dialog({
			title: __("Record Training History"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "explanation",
					options: `<p class="text-muted">${__(
						"For training given on paper before the system, on a document the employee's job is trained on. The record opens on the revision in effect; then record how and when the training was given, attach the certificate and sign Record History."
					)}</p>`,
				},
				{
					fieldtype: "Link",
					fieldname: "employee",
					label: __("Employee"),
					options: "Employee",
					reqd: 1,
					get_query: () => ({ filters: { status: "Active" } }),
				},
				{
					fieldtype: "Link",
					fieldname: "controlled_document",
					label: __("Document"),
					options: "Controlled Document",
					reqd: 1,
					get_query: () => ({ filters: { status: "Effective" } }),
				},
			],
			primary_action_label: __("Start"),
			primary_action({ employee, controlled_document }) {
				dialog.disable_primary_action();
				frappe
					.xcall("meddevice_suite.qms.training.start_history", {
						employee,
						controlled_document,
					})
					.then((name) => {
						dialog.hide();
						frappe.set_route("Form", "Training Record", name);
					})
					// Frappe has shown the server's refusal: the dialog stays open with the
					// employee and document chosen, to correct and start again.
					.catch(() => dialog.enable_primary_action());
			},
		});
		dialog.show();
	},
};
