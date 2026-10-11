// Quality Settings (URS-PLT-012, URS-PLT-013): a change names an approved change request that
// lists Quality Settings; the lookup offers only those (qms/change_control.py).
frappe.ui.form.on("Quality Settings", {
	setup(frm) {
		frm.set_query("change_request", () => ({
			query: "meddevice_suite.qms.change_control.approved_changes_for",
			filters: { reference_doctype: "Quality Settings", reference_name: "Quality Settings" },
		}));
	},
});
