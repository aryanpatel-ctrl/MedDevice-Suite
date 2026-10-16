// A field that is empty and stays empty is not an edit.
//
// When the form shows a record whose field is empty after it held a value (the server cleared it
// on save, as an Important File clears its "new version" file, date and note once it records the
// version), the control writes its own empty value back: a date picker reports its clearing as a
// selection and parses it as `undefined`, a text box sends "". frappe.model.set_value compares
// with `!==`, so that over the stored `null` counts as a change and the record shows "Not Saved"
// although nothing differs from what is stored, inviting a save that records nothing
// (URS-PLT-019; found in the browser walk-through of 2026-10-16, D9-FILE-02 and D9-LIC-01).
//
// Fixed once here, on the control every field type inherits, rather than per form or per field
// type: any field the desk shows can go from a value to empty this way. Only an empty value over
// an empty value is ignored: setting, changing or clearing a value is an edit and goes through as
// before, a caller that forces the value (`set_value(value, true)`) still does, and 0 is a value.
if (frappe.ui?.form?.Control) {
	const is_empty = (value) => value === undefined || value === null || value === "";
	const prototype = frappe.ui.form.Control.prototype;
	const validate_and_set_in_model = prototype.validate_and_set_in_model;
	prototype.validate_and_set_in_model = function (value, e, force_set_value = false) {
		if (!force_set_value && is_empty(value) && is_empty(this.get_model_value())) {
			return Promise.resolve();
		}
		return validate_and_set_in_model.call(this, value, e, force_set_value);
	};
}
