// Reminders (module 60): an occurrence of a recurring reminder is completed by its job, or QA,
// with a remark. Who may complete it is checked on the server (work_management/reminders.py);
// a system reminder completes itself when the work is done in its record.
frappe.ui.form.on("Reminder", {
	refresh(frm) {
		if (!frm.doc.schedule || frm.doc.status === "Completed") return;
		frm.add_custom_button(__("Mark Completed"), () => {
			frappe.prompt(
				{
					fieldname: "remark",
					fieldtype: "Small Text",
					label: __("What was done"),
					reqd: 1,
				},
				({ remark }) =>
					frappe
						.call("meddevice_suite.work_management.reminders.complete_occurrence", {
							name: frm.doc.name,
							remark,
						})
						.then(() => frm.reload_doc()),
				__("Complete {0}", [frm.doc.subject])
			);
		});
	},
});
