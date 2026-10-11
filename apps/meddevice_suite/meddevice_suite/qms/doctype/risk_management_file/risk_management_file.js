// A product family's risk management file (module 39). QA starts each revision of its analysis
// from here; once the file has an approved one, under an approved change request listing the file
// or an open risk review (URS-QMS-024). The server checks every rule (qms/risk_management.py).

frappe.ui.form.on("Risk Management File", {
	setup(frm) {
		frm.set_query("risk_management_plan", () => ({ filters: { status: "Effective" } }));
		frm.set_query("item", "items", () => ({ filters: { mds_item_type: "Finished Good" } }));
	},

	refresh(frm) {
		if (frm.is_new() || frm.is_dirty()) return;
		const in_progress = frm.doc.__onload?.revision_in_progress;
		if (in_progress) {
			frm.dashboard.set_headline_alert(
				__("Revision {0} is in progress.", [frappe.utils.escape_html(in_progress)]),
				"blue"
			);
			return;
		}
		if (!frappe.user.has_role("Quality Manager")) return;
		frm.add_custom_button(__("Start New Revision"), () => start_revision(frm)).addClass(
			"btn-primary"
		);
	},
});

function start_revision(frm) {
	const begin = (values = {}) =>
		frappe
			.xcall("meddevice_suite.qms.risk_management.start_revision", {
				risk_management_file: frm.doc.name,
				change_request: values.change_request || null,
				risk_review: values.risk_review || null,
			})
			.then((name) => frappe.set_route("Form", "Risk Assessment", name));
	if (!frm.doc.current_revision) return begin();
	const dialog = new frappe.ui.Dialog({
		title: __("Start New Revision"),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p>${__(
					"Name the approved change request that lists this file, or the open risk review this revision answers."
				)}</p>`,
			},
			{
				fieldtype: "Link",
				fieldname: "change_request",
				label: __("Change Request"),
				options: "Change Request",
				get_query: () => ({
					query: "meddevice_suite.qms.change_control.approved_changes_for",
					filters: {
						reference_doctype: "Risk Management File",
						reference_name: frm.doc.name,
					},
				}),
			},
			{
				fieldtype: "Link",
				fieldname: "risk_review",
				label: __("Risk Review"),
				options: "Risk Review",
				get_query: () => ({
					filters: { risk_management_file: frm.doc.name, status: "Open" },
				}),
			},
		],
		primary_action_label: __("Start"),
		primary_action(values) {
			dialog.hide();
			begin(values);
		},
	});
	dialog.show();
}
