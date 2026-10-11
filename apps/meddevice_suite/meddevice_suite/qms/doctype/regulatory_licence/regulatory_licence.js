// Regulatory licences (module 41): QA verifies a licence against its copy, rejects it, or
// withdraws a verified one, each with a signature; records an obligation done with its evidence
// and a submission to the authority. Every rule is checked on the server (qms/regulatory_licence.py).
const esign = frappe.provide("meddevice_suite.esign");
const LICENCE = "meddevice_suite.qms.regulatory_licence";

frappe.ui.form.on("Regulatory Licence", {
	refresh(frm) {
		if (frm.is_new() || frm.is_dirty() || !frappe.user.has_role("Quality Manager")) return;
		const { docstatus, status } = frm.doc;
		if (docstatus === 0) {
			frm.add_custom_button(__("Verify"), () =>
				esign.sign(frm, "Verify", "Verified", { ask_reason: true }).catch(() => {})
			).addClass("btn-primary");
			frm.add_custom_button(__("Reject"), () =>
				esign.sign(frm, "Reject", "Rejected", { ask_reason: true }).catch(() => {})
			);
		}
		if (docstatus === 1 && status === "Verified") {
			frm.add_custom_button(__("Withdraw"), () =>
				esign.sign(frm, "Withdraw", "Rejected", { ask_reason: true }).catch(() => {})
			);
			const open = (frm.doc.obligations || []).filter((row) => !row.done_on);
			if (open.length) {
				frm.add_custom_button(__("Obligation Done"), () =>
					frappe.prompt(
						[
							{
								fieldname: "row",
								fieldtype: "Select",
								label: __("Obligation"),
								reqd: 1,
								options: open.map((row) => ({
									value: row.name,
									label: `${row.obligation} (${row.due_date})`,
								})),
							},
							{
								fieldname: "evidence",
								fieldtype: "Link",
								options: "Important File",
								label: __("Evidence"),
								reqd: 1,
							},
						],
						(values) =>
							frappe
								.call(`${LICENCE}.complete_obligation`, {
									name: frm.doc.name,
									...values,
								})
								.then(() => frm.reload_doc()),
						__("Record an obligation as done")
					)
				);
			}
			frm.add_custom_button(__("Add Obligation"), () =>
				frappe.prompt(
					[
						{
							fieldname: "obligation",
							fieldtype: "Data",
							label: __("Obligation"),
							reqd: 1,
						},
						{ fieldname: "due_date", fieldtype: "Date", label: __("Due"), reqd: 1 },
					],
					(values) =>
						frappe
							.call(`${LICENCE}.add_obligation`, { name: frm.doc.name, ...values })
							.then(() => frm.reload_doc()),
					__("Add an obligation of the licence")
				)
			);
			frm.add_custom_button(__("Record Submission"), () =>
				frappe.prompt(
					[
						{
							fieldname: "submission_type",
							fieldtype: "Select",
							label: __("Submission"),
							reqd: 1,
							options: [
								"Device Master File",
								"Plant Master File",
								"Change Notification",
								"Renewal Application",
								"Other",
							],
						},
						{
							fieldname: "submitted_on",
							fieldtype: "Date",
							label: __("Submitted On"),
							reqd: 1,
						},
						{
							fieldname: "authority_reference",
							fieldtype: "Data",
							label: __("Authority Reference"),
							reqd: 1,
						},
						{
							fieldname: "important_file",
							fieldtype: "Link",
							options: "Important File",
							label: __("Copy"),
							reqd: 1,
						},
					],
					(values) =>
						frappe
							.call(`${LICENCE}.add_submission`, { name: frm.doc.name, ...values })
							.then(() => frm.reload_doc()),
					__("Record a submission to the authority")
				)
			);
		}
		if (frm.doc.verified_file_version && frm.doc.important_file) {
			frappe.db
				.get_value("Important File", frm.doc.important_file, "current_version")
				.then(({ message }) => {
					if (message && message.current_version > frm.doc.verified_file_version) {
						frm.dashboard.set_headline_alert(
							__(
								"The copy has a newer version ({0}) than the one QA verified ({1}).",
								[message.current_version, frm.doc.verified_file_version]
							),
							"orange"
						);
					}
				});
		}
	},
});
