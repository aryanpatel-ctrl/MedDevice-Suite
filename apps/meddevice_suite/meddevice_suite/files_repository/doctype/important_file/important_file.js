// Important files repository (module 61): a file's uploaders, or QA, archive it with a reason.
// Who may upload or archive is checked on the server (files_repository/files.py).
frappe.ui.form.on("Important File", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Active") return;
		frm.add_custom_button(__("Archive"), () =>
			frappe.prompt(
				{ fieldname: "reason", fieldtype: "Small Text", label: __("Why"), reqd: 1 },
				({ reason }) =>
					frappe
						.call("meddevice_suite.files_repository.files.archive", {
							name: frm.doc.name,
							reason,
						})
						.then(() => frm.reload_doc()),
				__("Archive {0}", [frm.doc.title])
			)
		);
	},
});
