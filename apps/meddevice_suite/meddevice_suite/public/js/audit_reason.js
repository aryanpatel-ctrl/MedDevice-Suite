// Reason for change (URS-CMP-011). Cancelling, amending or updating a submitted
// regulated record asks the user why, and stages the reason on the server just
// before the action is sent. The server refuses the action without one
// (meddevice_suite/compliance/audit_reason.py); this only asks for it in time.
//
// The reason is asked before Frappe's own cancel flow starts, because "Cancel All"
// cancels the linked records in a request of its own before the record itself.

const REASON_REQUIRED = {};

function requires_reason(doctype) {
	REASON_REQUIRED[doctype] ??= frappe
		.xcall("meddevice_suite.compliance.audit_reason.requires_reason", { doctype })
		.catch((error) => {
			delete REASON_REQUIRED[doctype]; // ask the server again next time
			throw error;
		});
	return REASON_REQUIRED[doctype];
}

const TITLES = {
	cancel: (name) => __("Reason for cancelling {0}", [name]),
	amend: (name) => __("Reason for amending {0}", [name]),
	update_after_submit: (name) => __("Reason for changing {0}", [name]),
};

function ask_reason(title) {
	return new Promise((resolve, reject) => {
		let given = false;
		const dialog = new frappe.ui.Dialog({
			title,
			fields: [
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					reqd: 1,
					description: __("Recorded in the audit trail with your name and the time."),
				},
			],
			primary_action_label: __("Continue"),
			primary_action({ reason }) {
				given = true;
				dialog.hide();
				resolve(reason);
			},
			on_hide() {
				if (!given) reject();
			},
		});
		dialog.show();
	});
}

// Resolves when the action may go ahead: no reason is needed, or one has been staged.
function with_reason(frm, action, name) {
	return requires_reason(frm.doctype).then((required) => {
		if (!required) return;
		return ask_reason(TITLES[action](name)).then((reason) =>
			frappe.xcall("meddevice_suite.compliance.audit_reason.stage_reason", {
				doctype: frm.doctype,
				name,
				action,
				reason,
			})
		);
	});
}

// ERPNext's Update Items changes a submitted order's lines in a request of its own
// (update_child_qty_rate), which the server refuses without a reason: it is asked first.
// ERPNext's forms look the function up when the button is clicked, so wrapping it once serves all.
function wrap_update_items() {
	const update_child_items = window.erpnext?.utils?.update_child_items;
	if (!update_child_items || update_child_items.mds_reason_for_change) return;
	const wrapped = (opts) =>
		with_reason(opts.frm, "update_after_submit", opts.frm.doc.name).then(
			() => update_child_items(opts),
			() => {}
		);
	wrapped.mds_reason_for_change = true;
	window.erpnext.utils.update_child_items = wrapped;
}

$(document).on("form-refresh", (event, frm) => {
	wrap_update_items();
	// One form object serves every record of its type; wrap its actions once.
	if (frm.mds_reason_for_change) return;
	frm.mds_reason_for_change = true;

	const savecancel = frm.savecancel.bind(frm);
	frm.savecancel = (btn, callback, on_error) =>
		with_reason(frm, "cancel", frm.doc.name).then(
			() => savecancel(btn, callback, on_error),
			() => frm.handle_save_fail(btn, on_error)
		);

	// A workflow action that cancels the record (module 48) asks too. Frappe freezes the
	// screen before its own before_workflow_action trigger, so the dialog comes first.
	const handle_workflow_action = frm.states.handle_workflow_action.bind(frm.states);
	frm.states.handle_workflow_action = (transition) => {
		const next = frappe.workflow.workflows[frm.doctype]?.states.find(
			(state) => state.state === transition.next_state
		);
		if (cint(next?.doc_status) !== 2) return handle_workflow_action(transition);
		return with_reason(frm, "cancel", frm.doc.name).then(
			() => handle_workflow_action(transition),
			() => {}
		);
	};

	const save = frm.save.bind(frm);
	frm.save = (save_action, callback, btn, on_error) => {
		const amending = frm.is_new() && frm.doc.amended_from;
		const action = amending
			? "amend"
			: save_action === "Update"
			? "update_after_submit"
			: null;
		if (!action) return save(save_action, callback, btn, on_error);

		const name = amending ? frm.doc.amended_from : frm.doc.name;
		return with_reason(frm, action, name).then(
			() => save(save_action, callback, btn, on_error),
			() => frm.handle_save_fail(btn, on_error)
		);
	};
});
