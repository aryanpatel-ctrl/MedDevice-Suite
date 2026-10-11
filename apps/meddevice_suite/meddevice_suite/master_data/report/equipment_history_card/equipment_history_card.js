// The equipment history card (module 62): everything that happened to one piece of equipment.
frappe.query_reports["Equipment History Card"] = {
	filters: [
		{
			fieldname: "asset",
			label: __("Equipment"),
			fieldtype: "Link",
			options: "Asset",
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			// Empty: from when the equipment was recorded.
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
	],
};
