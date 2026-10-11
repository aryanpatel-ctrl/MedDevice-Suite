// Licence register (module 41): each licence of the plant with its standing today.
frappe.query_reports["Licence Register"] = {
	filters: [
		{
			fieldname: "include_inactive",
			label: __("Include Rejected, Withdrawn and Superseded"),
			fieldtype: "Check",
		},
	],
};
