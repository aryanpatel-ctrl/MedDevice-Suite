// Tasks and approvals (module 59): the person assigned submits the task with what was done; its
// approver approves it, or returns it, with a signature; whoever raised it, or QA, may withdraw it
// before it is approved. Every rule is checked on the server (work_management/work_task.py).
const esign = frappe.provide("meddevice_suite.esign");
const TASK = "meddevice_suite.work_management.work_task";

frappe.ui.form.on("Work Task", {
	refresh(frm) {
		if (frm.is_new()) return;
		const user = frappe.session.user;
		const status = frm.doc.status;
		if (["Open", "Returned"].includes(status) && user === frm.doc.assigned_to) {
			frm.add_custom_button(__("Submit for Approval"), () =>
				frappe.prompt(
					{
						fieldname: "result",
						fieldtype: "Small Text",
						label: __("What was done"),
						reqd: 1,
						default: frm.doc.result,
					},
					({ result }) =>
						frappe
							.call(`${TASK}.submit_for_approval`, { name: frm.doc.name, result })
							.then(() => frm.reload_doc()),
					__("Submit {0} for approval", [frm.doc.name])
				)
			);
		}
		if (status === "Submitted" && user === frm.doc.approver) {
			frm.add_custom_button(__("Approve"), () =>
				esign.sign(frm, "Approve", "Approved", { ask_reason: true }).catch(() => {})
			);
			frm.add_custom_button(__("Return"), () =>
				esign.sign(frm, "Return", "Rejected", { ask_reason: true }).catch(() => {})
			);
		}
		const may_withdraw = user === frm.doc.owner || frappe.user.has_role("Quality Manager");
		if (["Open", "Submitted", "Returned"].includes(status) && may_withdraw) {
			frm.add_custom_button(__("Withdraw"), () =>
				frappe.prompt(
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Why"), reqd: 1 },
					({ reason }) =>
						frappe
							.call(`${TASK}.withdraw`, { name: frm.doc.name, reason })
							.then(() => frm.reload_doc()),
					__("Withdraw {0}", [frm.doc.name])
				)
			);
		}
	},
});
