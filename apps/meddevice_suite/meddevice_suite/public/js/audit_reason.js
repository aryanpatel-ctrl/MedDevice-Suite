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

$(document).on("form-refresh", (event, frm) => {
	// One form object serves every record of its type; wrap its actions once.
	if (frm.mds_reason_for_change) return;
	frm.mds_reason_for_change = true;

	const savecancel = frm.savecancel.bind(frm);
	frm.savecancel = (btn, callback, on_error) =>
		with_reason(frm, "cancel", frm.doc.name).then(
			() => savecancel(btn, callback, on_error),
			() => frm.handle_save_fail(btn, on_error)
		);

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
