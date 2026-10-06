// Verify a completed archive against the fingerprints recorded when it was made (module 54).
frappe.ui.form.on("Record Archive", {
	refresh(frm) {
		if (frm.doc.status !== "Completed") return;
		frm.add_custom_button(__("Verify"), () =>
			frappe
				.xcall("meddevice_suite.compliance.archive.verify", { archive: frm.doc.name })
				.then((problems) =>
					problems.length
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
