// A date, date-time or time field that is empty and stays empty is not an edit.
//
// When the form shows a record whose Date, Datetime or Time field is empty after it held a value
// (the server cleared it on save, as an Important File clears its "new version" fields, or the
// form now shows another record), the control clears its date picker. The picker reports the
// clearing as a selection, the control turns it into a "change" of the input, and parses the empty
// input as `undefined` (Date, Time) or "" (Datetime). frappe.model.set_value compares with `!==`,
// so that over the stored `null` counts as a change and the record shows "Not Saved" although
// nothing differs from what is stored, inviting a save that records nothing (URS-PLT-019).
//
// Fixed once here for every form, rather than per form, because any Date field the desk shows can
// go from a value to empty this way. Only an empty value over an empty value is ignored: setting,
// changing or clearing a date that holds a value is an edit and goes through as before, and a
// caller that forces the value (`set_value(value, true)`) still does.
if (frappe.ui?.form?.ControlDate) {
	const is_empty = (value) => value === undefined || value === null || value === "";
	const prototype = frappe.ui.form.ControlDate.prototype; // Datetime and Time inherit it
	const validate_and_set_in_model = prototype.validate_and_set_in_model;
	prototype.validate_and_set_in_model = function (value, e, force_set_value = false) {
		if (!force_set_value && is_empty(value) && is_empty(this.get_model_value())) {
			return Promise.resolve();
		}
		return validate_and_set_in_model.call(this, value, e, force_set_value);
	};
}
