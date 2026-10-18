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
	// Resolves with the name of the new revision.
	const begin = (values = {}) =>
		// The caller handles the refusal: the first revision leaves the form as it was, and the
		// dialog stays open.
		frappe.xcall("meddevice_suite.qms.risk_management.start_revision", {
			risk_management_file: frm.doc.name,
			change_request: values.change_request || null,
			risk_review: values.risk_review || null,
		});
	const open = (name) => frappe.set_route("Form", "Risk Assessment", name);
	if (!frm.doc.current_revision) {
		// Frappe has shown the server's refusal; the file stays as it was.
		begin()
			.then(open)
			.catch(() => {});
		return;
	}
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
			dialog.disable_primary_action();
			begin(values)
				.then((name) => {
					dialog.hide();
					open(name);
				})
				// Frappe has shown the server's refusal: the dialog stays open with the change
				// request or review named, to correct and start again.
				.catch(() => dialog.enable_primary_action());
		},
	});
	dialog.show();
}
