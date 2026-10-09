// UI tests, run with: bench --site <site> run-ui-tests meddevice_suite --headless
// Cypress comes from the frappe app, so this file must not require() it.
// `bench run-ui-tests` sets baseUrl and the admin password from the site config;
// the site name must resolve on the machine (`bench --site <site> add-to-hosts`).
module.exports = {
	defaultCommandTimeout: 20000,
	pageLoadTimeout: 30000,
	viewportWidth: 1400,
	viewportHeight: 960,
	video: false,
	// Before acting on a field, scroll it to the middle of the window, as a person would look at
	// it: scrolled to the top it lies under the form's fixed title bar, and Cypress refuses to
	// type into it (found in CI, 2026-10-09, on the incoming inspection's lot field).
	scrollBehavior: "center",
	retries: { runMode: 1, openMode: 0 },
	e2e: {
		specPattern: "cypress/integration/**/*.js",
		supportFile: "cypress/support/e2e.js",
		testIsolation: false,
	},
};
