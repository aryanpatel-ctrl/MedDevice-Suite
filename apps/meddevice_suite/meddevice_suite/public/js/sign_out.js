// Signing out starts the next person on a clean sign-in page.
//
// Frappe sends a user who signs out to /login?redirect-to=<the page they were on>,
// so whoever signs in next lands on that page. On a shared computer that shows the
// next person which record the last one had open, and a person not allowed to see
// it is met by a permission error instead of their own desk. A session that expires
// still returns its own user to their page; only a deliberate sign-out, which Frappe
// marks as `logged_out`, goes to a clean sign-in page.
if (frappe.Application) {
	const redirect_to_login = frappe.Application.prototype.redirect_to_login;
	frappe.Application.prototype.redirect_to_login = function () {
		if (this.logged_out) {
			window.location.href = "/login";
			return;
		}
		return redirect_to_login.call(this);
	};
}
