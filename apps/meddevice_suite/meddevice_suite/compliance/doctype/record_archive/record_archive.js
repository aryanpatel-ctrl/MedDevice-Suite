// Verify a completed archive against the fingerprints recorded when it was made (module 54).
frappe.ui.form.on("Record Archive", {
	refresh(frm) {
		if (frm.doc.status !== "Completed") return;
		frm.add_custom_button(__("Verify"), () =>
			frappe
				.xcall("meddevice_suite.compliance.archive.verify", { archive: frm.doc.name })
				.then((problems) =>
					!Array.isArray(problems)
						? frappe.msgprint({
								title: __("Verification started"),
								indicator: "blue",
								message: __(
									"This archive is large, so it is verified in the background. You will be notified of the result."
								),
						  })
						: problems.length
						? frappe.msgprint({
								title: __("Archive has changed"),
								indicator: "red",
								message: problems
									.map((problem) => frappe.utils.escape_html(problem))
									.join("<br>"),
						  })
						: frappe.msgprint({
								title: __("Archive verified"),
								indicator: "green",
								message: __(
									"Every file is exactly as it was when the archive was made."
								),
						  })
				)
		);
	},
});
