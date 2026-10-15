// Browser test sheet of data store D10, Equipment Master (modules 8, 38, 40, 62): the equipment
// register on ERPNext's asset and QA's signed approval of it, change control of what QA approved,
// calibration with the impact of a value found out of tolerance, preventive maintenance, IQ, OQ and
// PQ, QA's signed Correct of a completed record, the history card and the care schedule
// (meddevice_suite/master_data/equipment.py, qms/calibration.py, qms/preventive_maintenance.py,
// qms/qualification.py, qms/equipment_care.py). One test per line of the sheet, in its order, each
// signed in as the job the sheet names and done on the screens as that person would. Refusals are
// checked word for word; a refusal only an API call can reach is posted as that job.
//
// Since the sheet was written the code has gained, and the tests check where the sheet touches it:
//   an out-of-tolerance calibration lists the job cards of each workstation the instrument was at,
//   read from the audit trail, each naming its workstation (D10-T08);
//   a change of a qualified machine's workstation is a move, after which it is qualified again
//   from the IQ (D10-T16);
//   the completion's signature binds the impact rows the completion itself adds, and verifies
//   (D10-T08).
//
// The sheet names the pilot's records; the test site has none, so the run uses records of its own
// (meddevice_suite/qa/sheets/d10.py), new for each test, so no test depends on another:
//   Digital Caliper (Demo) -> an instrument of the test's own, recorded, approved and calibrated
//   as far as the test needs (the journey's caliper reads every other sheet's inspections and is
//   never taken out of service or corrected here);
//   the asset of D10-T01 or D10-T02 -> a new instrument at the stage the later test starts from;
//   Test Tipping Machine -> a machine of the sheet's own equipment item, needing what the test needs;
//   tags and serial numbers carry a tag of the run, so a later run does not reuse them.
// The out-of-tolerance calibrations of D10-T08 and D10-T09 are drafted by Maintenance in the set-up
// and completed on the screen: recording a calibration on the screen is D10-T06 and D10-T07.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const SHEET = "meddevice_suite.qa.sheets.d10";
const SIGN_ACTION = "meddevice_suite.compliance.signing.sign_action";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const LINE = ".grid-row-open"; // a line of a table, opened in its own form
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it

const QA = "QA Manager";
const QC = "QC Inspector";
const MAINTENANCE = "Maintenance";
const MANAGEMENT = "Management";

let password;
let users;
let data; // what the sheet works with (qa/sheets/d10.py prepare_sheet)
let TODAY; // the site's today, as the desk reads it

// What went wrong in the test: a refused or failed request, or an error message on screen, that
// the test did not expect. Every test ends by checking it is empty.
const run = { problems: [], expected: [] };

// --- Signing in -----------------------------------------------------------------------------

// Signs in as the job without writing the password into the command log, after leaving the
// previous person's page, as a person signs out before the next signs in (standard_flow.js).
function sign_in(job) {
	const email = users[job];
	leave_page();
	signed_in_as(email, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", email);
}

// The Administrator who runs the tests prepares what a test starts from (qa/sheets/d10.py).
function prepare(method, args = {}) {
	as_administrator();
	return cy.call(`${SHEET}.${method}`, args).then((r) => r.message);
}

// A session kept between tests is checked before it is used: one the server has since ended
// (found in Chrome, 2026-10-15: the Administrator's, late in the run) is signed in again.
function signed_in_as(user, pwd) {
	cy.session(
		["sheet-d10", user],
		() => {
			cy.request({
				url: "/api/method/login",
				method: "POST",
				body: { usr: user, pwd },
				log: false,
			});
		},
		{
			validate() {
				cy.request({ url: "/api/method/frappe.auth.get_logged_user", log: false })
					.its("body.message")
					.should("eq", user);
			},
		}
	);
}

function as_administrator() {
	leave_page();
	signed_in_as("Administrator", Cypress.env("adminPassword"));
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", "Administrator");
}

function leave_page() {
	forget_unsaved();
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
}

// A form with unsaved changes asks "Leave site?" in a native dialog, which a person answers and
// which would freeze the test; the person leaves a refused form as it is, so the test does too.
function forget_unsaved() {
	cy.window({ log: false }).then((win) => {
		const forms = Object.values(win.frappe?.views?.formview || {}).map((view) => view.frm);
		for (const frm of [win.cur_frm, ...forms]) {
			if (frm?.beforeUnloadListener) {
				win.removeEventListener("beforeunload", frm.beforeUnloadListener, {
					capture: true,
				});
			}
		}
	});
}

// --- Watching for problems ------------------------------------------------------------------

function plain(html) {
	return String(html ?? "")
		.replace(/<[^>]*>/g, " ")
		.replace(/&#39;|&#x27;/g, "'")
		.replace(/&quot;/g, '"')
		.replace(/&lt;/g, "<")
		.replace(/&gt;/g, ">")
		.replace(/&amp;/g, "&")
		.replace(/\s+/g, " ")
		.trim();
}

function parsed(body) {
	if (!body || typeof body === "object") return body || {};
	try {
		return JSON.parse(body);
	} catch {
		return {};
	}
}

function server_message(body) {
	const response = parsed(body);
	const messages = [response.exc_type].filter(Boolean);
	try {
		for (const message of JSON.parse(response._server_messages || "[]")) {
			messages.push(plain(JSON.parse(message).message));
		}
	} catch {
		// not Frappe's message list: the status says enough
	}
	return messages.join(": ");
}

function watch_requests() {
	cy.intercept({ url: API }, (req) => {
		req.on("response", (res) => {
			if (res.statusCode < 400) return;
			const message = server_message(res.body);
			const expected = run.expected.findIndex((pattern) => pattern.test(message));
			if (expected >= 0) {
				run.expected.splice(expected, 1);
			} else {
				const path = new URL(req.url).pathname;
				run.problems.push(`${req.method} ${res.statusCode} ${path} → ${message}`);
			}
		});
	});
}

// A pattern that matches `text` word for word.
function exact(text) {
	return new RegExp(String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
}

// The refusal the sheet expects: its message, and on screen its title before it.
function refusal(message, title = null) {
	return {
		server: exact(message),
		screen: title ? exact(`${title}: ${message}`) : exact(message),
	};
}

// The next refused request whose message matches is the one the step is about.
function expect_refusal(pattern) {
	cy.then(() => run.expected.push(pattern));
}

// Resolves true once `ready()` holds, false when it does not within the timeout.
function until(ready, timeout = TIMEOUT) {
	return new Promise((resolve) => {
		const started = Date.now();
		const check = () => {
			let done = false;
			try {
				done = Boolean(ready());
			} catch {
				done = false;
			}
			if (done) return resolve(true);
			if (Date.now() - started > timeout) return resolve(false);
			setTimeout(check, 50);
		};
		check();
	});
}

// Waits until no request has been in flight for quiet_ms, the screen is not frozen, and the open
// form is not about to reload (standard_flow.js). A line opened in its own form darkens the page
// behind it, which Frappe counts as one freeze; that one is the person's open line, not a wait.
function settle(quiet_ms = QUIET_MS) {
	return cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		let since = null;
		const frozen = () => win.frappe.dom.freeze_count > (win.cur_frm?.cur_grid ? 1 : 0);
		return until(() => {
			if (
				win.frappe.request.ajax_count > 0 ||
				frozen() ||
				win.cur_frm?.doc?.__needs_refresh
			) {
				since = null;
				return false;
			}
			since = since ?? Date.now();
			return Date.now() - since >= quiet_ms;
		}).then(
			(settled) =>
				expect(
					settled,
					`the page finished what it was doing (requests in flight: ${win.frappe.request.ajax_count}, screen frozen: ${win.frappe.dom.freeze_count})`
				).to.be.true
		);
	});
}

function message_dialog(win) {
	const dialog = win.frappe.msg_dialog;
	if (!dialog || !dialog.$wrapper.is(":visible")) return null;
	return {
		dialog,
		text: plain(
			`${dialog.$wrapper.find(".modal-title").text()}: ${dialog.$wrapper
				.find(".msgprint")
				.text()}`
		),
		// Frappe marks errors and refusals red and warnings orange.
		error: /\b(red|orange)\b/.test(dialog.indicator?.attr("class") || ""),
	};
}

function close_message(win) {
	return until(() => {
		if (!win.frappe.msg_dialog.$wrapper.is(":visible")) return true;
		win.frappe.hide_msgprint(true);
		return false;
	}, 5000);
}

// The test went as expected: no error message on screen, no unexpected refusal, and every
// refusal it expected has happened.
function no_problems(step) {
	settle();
	cy.window({ log: false }).then((win) => {
		const shown = message_dialog(win);
		if (shown?.error) run.problems.push(`the message "${shown.text}" is shown`);
		const problems = run.problems.splice(0);
		const missing = run.expected.splice(0);
		expect(problems, `${step}: problems\n${problems.join("\n")}\n`).to.be.empty;
		expect(missing.map(String), `${step}: refusals expected but not made`).to.be.empty;
	});
}

// The step's refusal is on screen as an error message saying `pattern`; it is closed.
function refused_with(pattern) {
	cy.window()
		.should((win) => {
			const shown = message_dialog(win);
			expect(shown, "an error message").to.not.equal(null);
			expect(shown.error, `"${shown.text}" is an error`).to.be.true;
			expect(shown.text).to.match(pattern);
		})
		.then({ timeout: 10000 }, (win) =>
			close_message(win).then((closed) => expect(closed, "the message closes").to.be.true)
		);
}

// A server call the signed-in user makes that must be refused; returns the refusal.
function refused_call(method, args) {
	return cy
		.window()
		.its("frappe.csrf_token")
		.then((csrf_token) =>
			cy.request({
				method: "POST",
				url: `/api/method/${method}`,
				body: args,
				headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf_token },
				failOnStatusCode: false,
			})
		)
		.then((response) => {
			expect(response.status, `${method} is refused`).to.be.within(400, 499);
			return server_message(response.body);
		});
}

// --- Using the desk -------------------------------------------------------------------------

function slug(doctype) {
	return doctype.toLowerCase().replace(/ /g, "-");
}

function open_form(doctype, name) {
	forget_unsaved();
	cy.visit(`/desk/${slug(doctype)}/${encodeURIComponent(name)}`);
	form_is(doctype, (doc) => doc.name === name);
	settle();
}

function new_form(doctype) {
	forget_unsaved();
	cy.visit(`/desk/${slug(doctype)}/new`);
	form_is(doctype, (doc) => doc.__islocal);
	settle();
}

function open_list(doctype, filters = {}) {
	forget_unsaved();
	const query = new URLSearchParams(filters).toString();
	// The list view itself, also for a record type that opens as a tree by default (Warehouse).
	cy.visit(`/desk/${slug(doctype)}/view/list${query ? `?${query}` : ""}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a list is open").to.equal("List");
		expect(win.cur_list?.doctype, "the list").to.equal(doctype);
	});
	settle();
}

// Opens a report with its filters set, as its menu link with the filters does; resolves with the
// lines it shows.
function open_report(name, filters = {}) {
	forget_unsaved();
	const query = new URLSearchParams(filters).toString();
	cy.visit(`/desk/query-report/${encodeURIComponent(name)}${query ? `?${query}` : ""}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a report is open").to.equal("query-report");
		expect(win.frappe.query_report?.report_name, "the report").to.equal(name);
	});
	settle();
	cy.window().should((win) => {
		for (const [field, value] of Object.entries(filters)) {
			expect(win.frappe.query_report.get_filter_value(field), field).to.equal(value);
		}
		expect(win.frappe.query_report.data, "the report's lines").to.be.an("array");
	});
	settle();
	return cy.window().then((win) => win.frappe.query_report.data);
}

// Waits for the form of `doctype` to show a record for which `holds(doc)` is true.
function form_is(doctype, holds = () => true) {
	cy.window().should((win) => {
		const frm = win.cur_frm;
		expect(win.frappe.get_route()[0], "a form is open").to.equal("Form");
		expect(frm?.doctype, "the form").to.equal(doctype);
		expect(frm.doc.name, "the record shown").to.equal(win.frappe.get_route()[2]);
		expect(Boolean(holds(frm.doc)), "the record is as expected").to.be.true;
	});
}

function current(win) {
	return win.cur_frm.doc;
}

// Brings a field into view as the person would: its tab opened, its section expanded.
function reveal(fieldname) {
	cy.window().then((win) => {
		const field = win.cur_frm.get_field(fieldname);
		if (field.tab && !field.tab.is_active()) {
			cy.get(`${FORM} .form-tabs-list [data-fieldname="${field.tab.df.fieldname}"]`).click();
		}
		if (field.section?.is_collapsed?.()) {
			field.section.head.trigger("click");
		}
	});
	cy.get(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`).first().scrollIntoView();
}

// The inputs a person can type into or choose from inside `container`.
function inputs_of(container) {
	return `${container} input:visible, ${container} select:visible, ${container} textarea:visible`;
}

function user_date(win, value) {
	return win.frappe.datetime.str_to_user(value);
}

function in_days(win, days) {
	return win.frappe.datetime.add_days(win.frappe.datetime.get_today(), days);
}

// Empties the field and types `value`, finding the field again for each step: clearing it can make
// the form redraw it (found in Chrome, 2026-10-15, on the work order's stores).
function type_afresh(selector, value) {
	settle();
	cy.get(first_of(selector)).clear();
	settle();
	cy.get(first_of(selector)).type(value, { delay: 0 });
}

// Types into a link field, found by `selector`, and chooses the matching entry of its list.
function choose(selector, value, reopen = null) {
	type_afresh(selector, value);
	settle();
	// A form redrawn by what an earlier field brought (a supplier's details, an item, its bill) can
	// take the typing away with the old input, or close a table cell again; the person opens the
	// field again as it now stands and types it once more.
	cy.get("body").then(($body) => {
		const $input = $body.find(selector).first();
		const listed = $body.find('[role="listbox"]:visible [role="option"]').length > 0;
		if (!listed || !$input.length || $input.val() !== value) {
			if (reopen) reopen();
			type_afresh(selector, value);
		}
	});
	// The open list of the field: only one is open at a time. An entry shows the record's title in
	// bold with its name right after it, with no space; the entry named `value` is the one.
	// The list is chosen from once the lookup's search has come back and it stops changing; the entry
	// is looked up again on every try, as Frappe redraws the list when the search answers.
	settle();
	lookup_list_settled();
	cy.get('[role="listbox"]:visible [role="option"]')
		.filter(
			(_, option) =>
				option ===
				entry_named(
					Cypress.$(option).closest('[role="listbox"]').find('[role="option"]'),
					value
				)
		)
		.should("have.length", 1)
		.click();
}

// Waits until the open lookup list shows entries and their number has not changed for a while: the
// list is redrawn as each search answers, and an entry clicked meanwhile is gone (found in CI).
function lookup_list_settled() {
	cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		let last = -1;
		let since = Date.now();
		return until(() => {
			const count = win.$('[role="listbox"]:visible [role="option"]').length;
			if (count !== last) {
				last = count;
				since = Date.now();
				return false;
			}
			return count > 0 && Date.now() - since >= QUIET_MS;
		}).then((settled) => expect(settled, "the lookup's list has settled").to.be.true);
	});
}

function entry_named($options, value) {
	const options = $options.toArray();
	const squeezed = (text) => String(text ?? "").replace(/\s+/g, "");
	return (
		options.find((option) => option.querySelector("strong")?.textContent.trim() === value) ||
		options.find((option) => squeezed(option.textContent).endsWith(squeezed(value))) ||
		options.find((option) => squeezed(option.textContent).includes(squeezed(value)))
	);
}

// A lookup's list can open again after a choice and lie over the fields below it; a person
// closes it before going on (standard_flow.js).
function lookup_fields_with_open_lists($body) {
	return $body
		.find('[role="listbox"]:visible')
		.toArray()
		.map((list) => $body.find(`input[aria-owns="${list.id}"]`))
		.filter(($field) => $field.length);
}

function close_open_lists() {
	cy.get("body", { log: false }).then(($body) => {
		for (const $field of lookup_fields_with_open_lists($body)) {
			$field[0].blur();
		}
	});
	cy.get("body", { log: false }).should(($body) =>
		expect(lookup_fields_with_open_lists($body), "lookup lists left open").to.have.length(0)
	);
}

// Puts `value` into an input as a person does for a field of `kind`: a lookup chosen from its
// list, a lot number typed in full as read off its label (a lookup of lots lists only those it
// thinks can be taken, and the sheet names others on purpose), a choice, a tick, a date, a text.
// Each action finds the field again by `selector`: the form may draw it anew in between.
function enter($input, value, kind, selector, reopen = null) {
	cy.window().then((win) => {
		if (kind === "Link") choose(selector, value, reopen);
		else if (kind === "Select") cy.get(first_of(selector)).select(value);
		else if (kind === "Check") cy.get(first_of(selector))[value ? "check" : "uncheck"]();
		else if (kind === "Date") type_and_leave(selector, user_date(win, value));
		else type_and_leave(selector, String(value));
	});
}

// The first match of each part of a selector list, as one query Cypress can run again when the
// page redraws (a chained .first() cannot be run again).
function first_of(selector) {
	return selector
		.split(",")
		.map((part) => `${part.trim()}:first`)
		.join(", ");
}

// Types into a field and leaves it, as the person would.
function type_and_leave(selector, text) {
	type_afresh(selector, text);
	// A date picker may already have taken the focus away.
	cy.document().then((doc) => {
		const active = doc.activeElement;
		if (active && Cypress.$(active).is(selector)) active.blur();
	});
}

function stored_as(stored, value, label) {
	if (typeof value === "number") expect(stored, label).to.be.closeTo(value, 1e-6);
	else expect(stored, label).to.equal(value);
}

// A person types the next field once the form has finished updating from the last one.
function fill(fieldname, value, kind = "Data") {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	const selector = inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`);
	cy.get(selector)
		.first()
		.then(($input) => enter($input, value, kind, selector));
	cy.window().should((win) => stored_as(current(win)[fieldname], value, fieldname));
}

function rows(win, table) {
	return current(win)[table] || [];
}

// Makes sure the table has `count` rows, adding them with its Add Row button.
function rows_up_to(table, count) {
	cy.window().then((win) => {
		const missing = count - rows(win, table).length;
		for (let i = 0; i < missing; i++) {
			cy.get(`${FORM} .frappe-control[data-fieldname="${table}"] .grid-add-row`).click();
		}
	});
	cy.window().should((win) => expect(rows(win, table)).to.have.length(count));
}

function cell(table, idx, fieldname) {
	return `${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] [data-fieldname="${fieldname}"]`;
}

function fill_row(table, idx, fieldname, value, kind = "Data") {
	settle();
	close_dialogs(); // and the Getting Started panel
	close_open_lists();
	cy.get(`${cell(table, idx, fieldname)}:first`).click();
	// Typed once the clicked cell has the cursor (standard_flow.js).
	cy.get(inputs_of(cell(table, idx, fieldname)))
		.first()
		.should("have.focus");
	cy.get(inputs_of(cell(table, idx, fieldname)))
		.first()
		.then(($input) =>
			enter($input, value, kind, inputs_of(cell(table, idx, fieldname)), () =>
				cy.get(`${cell(table, idx, fieldname)}:first`).click()
			)
		);
	cy.window().should((win) =>
		stored_as(rows(win, table)[idx - 1][fieldname], value, `line ${idx} ${fieldname}`)
	);
}

// Opens a line of a table in its own form, where the fields the table does not show are.
function open_line(table, idx) {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(table); // the table's tab, if another tab is open
	close_onboarding();
	cy.get(
		`${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] .row-index:first`
	).click();
	cy.get(LINE).should("be.visible");
	cy.window().should((win) => {
		expect(win.cur_frm.cur_grid?.doc?.parentfield, "the table").to.equal(table);
		expect(win.cur_frm.cur_grid.doc.idx, "the line").to.equal(idx);
	});
}

function fill_line(fieldname, value, kind = "Data") {
	settle();
	close_dialogs();
	close_open_lists();
	// A line's own form is longer than the window; the person scrolls to the field first. A field
	// Cypress cannot reach there (in a folded section, or under the line's fixed layer: found in
	// Chrome, 2026-10-15) is entered as data on the line; the refusals that follow are the test.
	const control = `${LINE} .frappe-control[data-fieldname="${fieldname}"]`;
	const selector = inputs_of(control);
	cy.get(control)
		.first()
		.then(($control) => {
			$control[0].scrollIntoView({ block: "center" });
			const $input = $control
				.find("input, select, textarea")
				.filter((_, element) => Cypress.dom.isVisible(element));
			if ($input.length) {
				enter($input.first(), value, kind, selector);
			} else {
				cy.window().then((win) => {
					const line = win.cur_frm.cur_grid.doc;
					return win.frappe.model.set_value(line.doctype, line.name, fieldname, value);
				});
			}
		});
	cy.window().should((win) => stored_as(win.cur_frm.cur_grid.doc[fieldname], value, fieldname));
}

function close_line() {
	settle();
	close_open_lists();
	// Closed as Frappe closes it: its collapse button may lie scrolled out of view, and the middle
	// of its heading can land on Delete.
	cy.window().then((win) => win.cur_frm.cur_grid?.toggle_view(false));
	cy.get(LINE).should("not.exist");
	settle();
}

function button(label) {
	settle(); // the form has finished drawing its buttons
	close_onboarding();
	// One query, so a button the form draws again before the click is found again.
	return cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible:first`);
}

function offered(label, expected) {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible`).should(
		expected ? "exist" : "not.exist"
	);
}

function from_menu(group, label) {
	settle();
	const menu = `${FORM} .inner-group-button[data-label="${encodeURIComponent(group)}"]`;
	cy.get(`${menu} > button`).click();
	cy.get(`${menu} .dropdown-item[data-label="${encodeURIComponent(label)}"]`).click();
}

function primary_action() {
	settle(); // the toolbar has finished redrawing
	return cy.get(`${FORM} .page-actions .primary-action:visible`);
}

function save() {
	primary_action().should("contain", "Save").click();
	cy.window().should((win) => {
		const frm = win.cur_frm;
		expect(frm.is_new(), "saved").to.not.be.ok;
		expect(frm.is_dirty(), "nothing left unsaved").to.not.be.ok;
	});
	settle();
}

// Save is pressed and the server refuses it with the sheet's message; the form stays as it was.
function save_refused({ server, screen }, label = "Save") {
	expect_refusal(server);
	primary_action().should("contain", label).click();
	refused_with(screen);
	settle();
	close_dialogs();
}

// The current dialog has finished fading in; a dialog closing behind it may still be fading out
// (found in Chrome, 2026-10-15: the submit confirmation over a closing message).
function opened_fully() {
	cy.window().should((win) => {
		const $wrapper = win.cur_dialog?.$wrapper;
		expect($wrapper?.is(".modal.show"), "a dialog is open").to.equal(true);
		expect($wrapper.css("opacity"), "it has finished opening").to.equal("1");
	});
}

// ERPNext's "Getting Started" panel floats over the right of the form and covers a table's right-hand
// columns (found in CI, 2026-10-15); the person closes it with its cross before going on.
function close_onboarding() {
	cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) =>
		until(() => {
			const $panel = win.$(".onb-panel:visible");
			if (!$panel.length) return true;
			$panel.find(".onb-header-actions button").last()[0]?.click();
			return false;
		}).then((closed) => expect(closed, "the Getting Started panel closes").to.be.true)
	);
}

// Closes whatever dialog is left over the form, the topmost first, as its cross closes it, until
// none is left: the batch picker ERPNext opens late on a line, or a message, would otherwise lie
// over the field the person types into next (found in Chrome, 2026-10-15, in D4-T12). A refusal
// left on screen is a problem of the step and is reported.
function close_dialogs() {
	cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		const reported = new Set();
		return until(() => {
			const open = win.$(".modal.show").length > 0 || win.cur_dialog?.display;
			if (!open) return true;
			const shown = message_dialog(win);
			if (shown?.error && !reported.has(shown.text)) {
				reported.add(shown.text);
				run.problems.push(`the message "${shown.text}" was left on screen`);
			}
			if (shown) win.frappe.hide_msgprint(true);
			else if (win.cur_dialog?.display) win.cur_dialog.hide();
			return false;
		}).then((closed) => expect(closed, "no dialog left over the form").to.be.true);
	});
	close_onboarding();
}

// Waits for the dialog that has just opened to finish opening (standard_flow.js).
function open_dialog() {
	opened_fully();
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the dialog has finished opening").to.equal(true);
		expect(win.cur_dialog.$wrapper.is(".modal.show"), "it is the dialog shown").to.equal(true);
	});
	return cy.get(".modal.show").last();
}

// --- The signing dialog (compliance/esign.js) ----------------------------------------------------

function enter_reason(reason) {
	cy.get('.modal.show textarea[data-fieldname="reason"]').click().clear().type(reason);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
}

function enter_password(given = password) {
	cy.get('.modal.show input[data-fieldname="password"]')
		.click()
		.clear()
		.type(given, { log: false });
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("password")).to.equal(given));
}

function press_sign() {
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
}

// The signing dialog is closed by its cross, signing nothing.
function close_signing() {
	cy.window().should((win) => expect(win.frappe.msg_dialog.$wrapper.is(":visible")).to.be.false);
	cy.window().should((win) => expect(win.cur_dialog?.title).to.match(/^Sign: /));
	cy.window()
		.its("cur_dialog")
		.then((dialog) =>
			cy.wrap(dialog.$wrapper.find(".btn-modal-close:visible").first()).click()
		);
	cy.get(".modal.show").should("not.exist");
	settle();
}

function banner(color, text) {
	cy.contains(`${FORM} .form-message.${color}`, text).should("be.visible");
}

function signed_as(action, meaning) {
	cy.window().should((win) => {
		const signed = (current(win).__onload?.signatures || []).map((s) => [s.action, s.meaning]);
		expect(signed, "electronic signatures").to.deep.include([action, meaning]);
	});
	cy.contains(FORM, "Electronic signatures").should("be.visible");
}

// --- Reading what is stored, as the signed-in person ----------------------------------------

function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

function stored_values(doctype, name, fieldname) {
	return cy
		.call("frappe.client.get_value", { doctype, filters: { name }, fieldname })
		.then((r) => r.message);
}

// The person is still signed in as the job: a refused signature does not end the session.
function still_signed_in(job) {
	cy.request({ url: "/api/method/frappe.auth.get_logged_user", log: false })
		.its("body.message")
		.should("eq", users[job]);
	cy.window().its("frappe.session.user").should("eq", users[job]);
}

// --- The form's menus and buttons -------------------------------------------------------------

// The form's ⋯ menu, opened to read what it offers, then closed again. Its items are drawn in a
// dropdown of their own, outside the menu's button group; an item's label is followed by its
// keyboard shortcut, if it has one.
function form_menu_offers(label, expected) {
	settle();
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
	cy.get(".dropdown-menu.show").should("be.visible");
	cy.get(".dropdown-menu.show .menu-item-label").should(($labels) => {
		const labels = $labels.toArray().map((item) => item.textContent.trim());
		if (expected) expect(labels, "the menu").to.include(label);
		else expect(labels, "the menu").to.not.include(label);
	});
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
}

function from_form_menu(label) {
	settle();
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
	cy.get(".dropdown-menu.show")
		.contains(".dropdown-item", new RegExp(`^\\s*${label}(\\s|$)`))
		.click();
}

// A group of buttons (Assess Impact) is on the form, or not.
function group_offered(group, expected) {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	cy.get(
		`${FORM} .inner-group-button[data-label="${encodeURIComponent(group)}"]:visible`
	).should(expected ? "exist" : "not.exist");
}

// The form offers no Save or Submit: the record is read-only for the person, or completed.
function nothing_to_save() {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	cy.get(`${FORM} .page-actions`).should(($actions) => {
		const labels = $actions
			.find(".primary-action:visible")
			.toArray()
			.map((action) => action.textContent.trim());
		expect(labels, "the form's main action").to.not.include("Save");
		expect(labels, "the form's main action").to.not.include("Submit");
	});
}

// A file attached to a field as the attach control does: uploaded privately, its address set.
function attach(fieldname, name) {
	upload_private_file(name).then((url) =>
		cy.window().then((win) => win.cur_frm.set_value(fieldname, url))
	);
	cy.window().should((win) => expect(current(win)[fieldname], fieldname).to.be.ok);
}

// A private file uploaded by the signed-in user, as the attach control does; its address.
function upload_private_file(name) {
	return cy.fixture("controlled-document.pdf", null).then((pdf) =>
		cy.window({ log: false }).then((win) => {
			const form = new win.FormData();
			form.append("file", new win.Blob([pdf], { type: "application/pdf" }), name);
			form.append("is_private", "1");
			return win
				.fetch("/api/method/upload_file", {
					method: "POST",
					body: form,
					headers: { "X-Frappe-CSRF-Token": win.frappe.csrf_token },
				})
				.then((response) => response.json())
				.then((r) => r.message.file_url);
		})
	);
}

// --- The signing dialog (compliance/esign.js) ----------------------------------------------------

const STATEMENT = {
	Approved: "I approve this.",
	Verified: "I have verified this.",
	Reviewed: "I have reviewed this and found it fit for approval.",
	Rejected: "I reject this.",
};
const WRONG_PASSWORD = "Incorrect password. The signature was not recorded.";

// Opens the signing dialog of the act from its button, or from its group of buttons.
function open_signing(label, { act = label, group = null } = {}) {
	settle();
	if (group) from_menu(group, label);
	else button(label).click();
	opened_fully();
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal(`Sign: ${act}`);
	});
}

// The dialog states what the signature means.
function statement_is(meaning) {
	cy.get(".modal.show").last().should("contain", `${meaning}: ${STATEMENT[meaning]}`);
}

// Signs the act with the run's password, and the reason when the act asks one; the form reloads
// the signed record (or opens the record the act opened).
function sign(label, { act = label, group = null, meaning = null, reason = null } = {}) {
	open_signing(label, { act, group });
	if (meaning) statement_is(meaning);
	if (reason) enter_reason(reason);
	enter_password();
	press_sign();
	cy.get(".modal.show").should("not.exist");
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

// An act the person's job may not sign, or not yet, posted as the signing dialog sends it: the
// refusal comes before the password is checked, so none is given.
function refused_signing(doctype, name, action, reason = "Not mine to sign (browser test)") {
	return refused_call(SIGN_ACTION, {
		doctype,
		name,
		action,
		password: "not-checked",
		reason,
	});
}

function signatures_bind(doctype, name, acts) {
	prepare("signatures_bind", { doctype, name }).then((bound) => {
		expect(bound.chain, `the chain of signatures on ${name}`).to.be.true;
		expect(
			bound.signatures.map((s) => [s.action, s.unchanged]),
			`each signature on ${name} binds what is stored`
		).to.deep.equal(acts.map((act) => [act, true]));
	});
}

// Verify Integrity on the form: the signatures on the record verify (the trail's anchors are
// another sheet's).
function integrity_verified() {
	from_menu("View", "Verify Integrity");
	cy.window()
		.should((win) => {
			const shown = message_dialog(win);
			expect(shown, "the integrity check's answer").to.not.equal(null);
			expect(shown.text).to.match(/Integrity (verified|problem found)/);
			expect(shown.text, "the signatures").to.not.contain(
				"The signatures on this record do not verify."
			);
		})
		.then({ timeout: 10000 }, (win) =>
			close_message(win).then((closed) => expect(closed).to.be.true)
		);
}

// --- The sheet's forms --------------------------------------------------------------------------

// QA records a piece of equipment on ERPNext's asset form, not yet saved: ERPNext's fields, then the
// Equipment section and its care, each [field, value, kind].
function asset_form({ item, name, equipment }) {
	new_form("Asset");
	cy.window().then((win) => {
		if (!current(win).company) fill("company", data.company, "Link");
	});
	fill("item_code", item, "Link");
	fill("asset_name", name);
	fill("location", data.location, "Link");
	fill("asset_type", "Existing Asset", "Select");
	fill("purchase_date", TODAY, "Date");
	fill("available_for_use_date", TODAY, "Date");
	fill("net_purchase_amount", 15000);
	for (const [fieldname, value, kind = "Data"] of equipment) fill(fieldname, value, kind);
}

// A point of a calibration of the 100 mm gauge block: found and left in tolerance.
const IN_TOLERANCE = {
	parameter: "Length, gauge block 100 mm",
	unit: "mm",
	nominal: 100,
	lower_limit: 99.98,
	upper_limit: 100.02,
	as_found: 100.01,
	as_left: 100,
};

// Maintenance records a calibration on its form, not yet saved.
function calibration_form({
	asset,
	by = "External laboratory",
	laboratory = null,
	certificate_number = null,
	certificate = null,
	point = IN_TOLERANCE,
}) {
	new_form("Calibration Record");
	fill("asset", asset, "Link");
	fill("performed_on", TODAY, "Date");
	fill("calibrated_by", by, "Select");
	if (laboratory) fill("laboratory", laboratory, "Link");
	if (certificate_number) fill("certificate_number", certificate_number);
	if (certificate) attach("certificate", certificate);
	rows_up_to("points", 1);
	open_line("points", 1);
	for (const [fieldname, value] of Object.entries(point)) fill_line(fieldname, value);
	close_line();
}

// Maintenance records preventive maintenance on its form, not yet saved; checks are [check, outcome].
function maintenance_form({ asset, checks, actions, downtime }) {
	new_form("Maintenance Record");
	fill("asset", asset, "Link");
	fill("performed_on", TODAY, "Date");
	rows_up_to("checks", checks.length);
	checks.forEach(([check, outcome], index) => {
		open_line("checks", index + 1);
		fill_line("check", check);
		fill_line("outcome", outcome, "Select");
		close_line();
	});
	fill("actions_performed", actions);
	fill("downtime_hours", downtime);
}

const PASSED_TEST = {
	test: "Unit installed as the protocol states",
	criteria: "As stated in the protocol",
	observed: "As stated",
	outcome: "Pass",
};

// An IQ, OQ or PQ on its form, not yet saved.
function qualification_form({ asset, stage, protocol, tests, occasion = "Initial" }) {
	new_form("Equipment Qualification");
	fill("asset", asset, "Link");
	fill("stage", stage, "Select");
	fill("occasion", occasion, "Select");
	fill("protocol", protocol, "Link");
	fill("performed_on", TODAY, "Date");
	rows_up_to("tests", tests.length);
	tests.forEach((test, index) => {
		open_line("tests", index + 1);
		fill_line("test", test.test);
		fill_line("acceptance_criteria", test.criteria);
		fill_line("observed", test.observed);
		fill_line("outcome", test.outcome, "Select");
		close_line();
	});
}

// The line of the equipment on the Equipment Care Schedule.
function care_of(asset) {
	return open_report("Equipment Care Schedule").then((lines) => {
		const line = lines.find((each) => each.asset === asset);
		expect(line, `${asset} on the care schedule`).to.be.ok;
		return line;
	});
}

function not_fit_because(asset) {
	return care_of(asset).then((line) => line.not_fit || "");
}

// The summary above the history card: label -> value as shown.
function report_summary() {
	return cy
		.get(`${FORM} .report-summary .summary-item`)
		.then(($items) =>
			Object.fromEntries(
				$items
					.toArray()
					.map((item) => [
						Cypress.$(item).find(".summary-label").text().trim(),
						Cypress.$(item).find(".summary-value").text().trim(),
					])
			)
		);
}

// --- The sheet ------------------------------------------------------------------------------

context("D10 Equipment Master: the browser test sheet, as each job", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password }));
		cy.then(() => cy.call(`${SHEET}.prepare_sheet`, { password })).then(({ message }) => {
			data = message;
		});
		cy.window().then((win) => {
			TODAY = win.frappe.datetime.get_today();
		});
	});

	beforeEach(() => {
		run.problems.length = 0;
		run.expected.length = 0;
		watch_requests();
		// A desk call the server refuses rejects its promise with nothing, which some ERPNext form
		// scripts leave unhandled; the refused request itself is reported by watch_requests with the
		// server's message. Any other script error is a problem of the step.
		cy.on("uncaught:exception", (error, _runnable, promise) => {
			const message = error?.message;
			if (!(promise && (message === undefined || message === "undefined"))) {
				run.problems.push(`script error: ${message}`);
			}
			return false;
		});
	});

	it("[URS-MST-010] D10-T01 QA records new equipment and the form says what approval still needs", () => {
		prepare("unique_tag").then((tag) => {
			sign_in(QA);
			asset_form({
				item: data.instrument_item,
				name: `Test Caliper 2 ${tag}`,
				equipment: [
					["mds_equipment_tag", `QC-CAL-002-${tag}`],
					["mds_equipment_class", "Measuring instrument", "Select"],
					["mds_manufacturer", "Demo Instruments"],
					["mds_model", "DC-150"],
				],
			});
			save();
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.mds_equipment_approval, "Approval by QA").to.equal("Draft");
				expect(doc.mds_serial_number || null).to.equal(null);
				expect(doc.mds_needs_calibration).to.equal(0);
			});
			banner(
				"orange",
				"Not yet approved by QA, so no inspection uses it. Still needed: its manufacturer's serial number; calibration (a measuring instrument is calibrated)."
			);
			offered("Approve Equipment", false);

			fill("mds_serial_number", "DEMO-0002");
			fill("mds_needs_calibration", 1, "Check");
			fill("mds_calibration_interval_days", 365);
			fill("mds_affects_quality", 1, "Check");
			save();
			banner(
				"orange",
				"Not yet approved by QA, so no inspection uses it. Ready for QA's approval."
			);
			offered("Approve Equipment", true);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.mds_equipment_approval, "Approval by QA").to.equal("Draft");
				expect(doc.mds_version, "Version").to.equal(1);
				expect(doc.mds_effective_from, "Version Effective From").to.equal(TODAY);
			});
			no_problems("D10-T01");
		});
	});

	it("[URS-MST-010] D10-T02 QA approves equipment with a signature; a wrong password approves nothing", () => {
		const made = {};
		prepare("prepare_instrument", { password, stage: "ready" }).then((asset) => {
			made.asset = asset.asset;
		});
		cy.then(() => prepare("prepare_instrument", { password, stage: "ready" })).then(
			(asset) => {
				made.other = asset.asset;
			}
		);
		const reason = "Received and checked, certificate on file";
		cy.then(() => {
			sign_in(QA);
			open_form("Asset", made.asset);
			banner("orange", "Ready for QA's approval.");
			open_signing("Approve Equipment", { act: "Approve" });
			statement_is("Approved");
			enter_reason(reason);
			enter_password(`${password}-wrong`);
			const wrong = refusal(WRONG_PASSWORD);
			expect_refusal(wrong.server);
			press_sign();
			refused_with(wrong.screen);
			cy.window().should((win) =>
				expect(current(win).mds_equipment_approval, "nothing approved").to.equal("Draft")
			);
			stored_values("Asset", made.asset, ["mds_equipment_approval"]).then((values) =>
				expect(values.mds_equipment_approval, "stored").to.equal("Draft")
			);
			still_signed_in(QA);

			// The dialog stays open with the reason; the password is entered again.
			enter_password();
			press_sign();
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			banner(
				"green",
				"Equipment approved by QA; what was approved changes under change control."
			);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.mds_equipment_approval, "Approval by QA").to.equal("Approved");
				expect(doc.mds_equipment_approved_by, "Approved By").to.equal(users[QA]);
				expect(String(doc.mds_equipment_approved_at).slice(0, 10), "Approved At").to.equal(
					TODAY
				);
				const signature = (doc.__onload?.signatures || []).find(
					(s) => s.action === "Approve"
				);
				expect(signature?.meaning, "Meaning").to.equal("Approved");
				expect(signature?.reason, "Reason").to.equal(reason);
			});
			cy.contains(`${FORM} .mds-signatures`, reason).should("be.visible");
			no_problems("D10-T02, QA");

			sign_in(MAINTENANCE);
			open_form("Asset", made.other);
			cy.window().should((win) =>
				expect(current(win).mds_equipment_approval).to.equal("Draft")
			);
			offered("Approve Equipment", false);
			no_problems("D10-T02");
		});
	});

	it("[URS-MST-010] D10-T03 A second piece of equipment cannot reuse a tag", () => {
		prepare("prepare_instrument", { password, stage: "approved" }).then((made) => {
			sign_in(QA);
			asset_form({
				item: data.instrument_item,
				name: `Test Caliper 3 ${made.tag}`,
				equipment: [["mds_equipment_tag", made.tag]],
			});
			save_refused(
				refusal(
					`Equipment tag ${made.tag} is on ${made.asset} already; each piece of equipment has its own.`
				)
			);
			cy.window().should((win) => expect(win.cur_frm.is_new(), "not saved").to.be.ok);

			// Duplicate on the approved asset: a copy without the tag, serial number or approval.
			open_form("Asset", made.asset);
			from_form_menu("Duplicate");
			form_is("Asset", (doc) => doc.__islocal);
			settle();
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.mds_equipment_tag || null, "Equipment Tag").to.equal(null);
				expect(doc.mds_serial_number || null, "Manufacturer's Serial Number").to.equal(
					null
				);
				expect(doc.mds_equipment_approval, "Approval by QA").to.equal("Draft");
				expect(doc.mds_equipment_approved_by || null, "Approved By").to.equal(null);
				expect(doc.mds_model, "the rest is copied").to.equal("DC-150 (demo)");
			});
			no_problems("D10-T03");
		});
	});

	it("[URS-MST-010] D10-T04 Approved equipment changes only under an approved change request", () => {
		prepare("prepare_instrument", { password, stage: "approved" }).then((made) => {
			sign_in(QA);
			open_form("Asset", made.asset);
			cy.window().should((win) =>
				expect(current(win).mds_version, "Version").to.equal(made.version)
			);

			// Step 2: the model, with no change request.
			fill("mds_model", "DC-200");
			save_refused(
				refusal(
					`Asset ${made.asset} is in use: name the approved change request that lists it to change it.`,
					"Change control"
				)
			);

			// Step 3: reloaded; its status changes freely, both ways.
			open_form("Asset", made.asset);
			cy.window().should((win) => expect(current(win).mds_model).to.equal("DC-150 (demo)"));
			fill("mds_equipment_status", "Out of service", "Select");
			save();
			cy.window().should((win) => {
				expect(current(win).mds_equipment_status).to.equal("Out of service");
				expect(current(win).mds_version, "Version").to.equal(made.version);
			});
			fill("mds_equipment_status", "In service", "Select");
			save();
			cy.window().should((win) => {
				expect(current(win).mds_equipment_status).to.equal("In service");
				expect(current(win).mds_version, "Version").to.equal(made.version);
			});
			no_problems("D10-T04, its status");

			// The model again, under an approved change request that lists the asset.
			prepare("prepare_change", { password, asset: made.asset }).then((change) => {
				sign_in(QA);
				open_form("Asset", made.asset);
				fill("mds_model", "DC-200");
				fill("mds_change_request", change, "Link");
				save();
				cy.window().should((win) => {
					const doc = current(win);
					expect(doc.mds_model).to.equal("DC-200");
					expect(doc.mds_version, "Version").to.equal(made.version + 1);
					expect(doc.mds_effective_from, "Version Effective From").to.equal(TODAY);
					expect(doc.mds_changed_under, "Last Changed Under").to.equal(change);
					expect(
						doc.mds_change_request || null,
						"Change Request for This Change"
					).to.equal(null);
				});
			});
			no_problems("D10-T04");
		});
	});

	it("[URS-EQP-006] D10-T05 An inspection reading cannot name equipment that is out of service", () => {
		const made = {};
		prepare("prepare_instrument", { password, stage: "calibrated" }).then((caliper) => {
			made.caliper = caliper.asset;
		});
		cy.then(() => prepare("prepare_inspection", { password })).then((inspection) => {
			made.inspection = inspection.inspection;
		});
		cy.then(() => {
			sign_in(QA);
			open_form("Asset", made.caliper);
			fill("mds_equipment_status", "Out of service", "Select");
			save();

			sign_in(QC);
			open_form("Quality Inspection", made.inspection);
			cy.window().should((win) =>
				expect(rows(win, "readings")[1].specification).to.equal("Dimension (mm)")
			);
			fill_row("readings", 2, "mds_instrument", made.caliper, "Link");
			save_refused(
				refusal(
					`Instrument ${made.caliper} is not used for an inspection: it is out of service.`,
					"Equipment not usable"
				)
			);
			no_problems("D10-T05, out of service");

			sign_in(QA);
			open_form("Asset", made.caliper);
			fill("mds_equipment_status", "In service", "Select");
			save();

			sign_in(QC);
			open_form("Quality Inspection", made.inspection);
			fill_row("readings", 2, "mds_instrument", made.caliper, "Link");
			save();
			cy.window().should((win) =>
				expect(
					rows(win, "readings")[1].mds_instrument,
					"the reading's instrument"
				).to.equal(made.caliper)
			);
			no_problems("D10-T05");
		});
	});

	it("[URS-EQP-001] D10-T06 Maintenance completes an external calibration with a signature", () => {
		prepare("prepare_instrument", { password, stage: "approved" }).then((made) => {
			sign_in(MAINTENANCE);
			calibration_form({
				asset: made.asset,
				laboratory: data.laboratory,
				certificate_number: "DCL-TEST-01",
				certificate: `calibration-certificate-${made.tag}.pdf`,
			});
			save();
			cy.window().should((win) => {
				const doc = current(win);
				const [point] = rows(win, "points");
				expect(point.found_in_tolerance, "Found in Tolerance").to.equal(1);
				expect(point.left_in_tolerance, "Left in Tolerance").to.equal(1);
				expect(doc.result, "Result").to.equal("Pass");
				expect(doc.out_of_tolerance, "Found Out of Tolerance").to.equal(0);
			});
			sign("Complete", { meaning: "Verified" });
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "completed").to.equal(1);
				expect(doc.interval_days, "Interval (days)").to.equal(365);
				expect(doc.next_due_date, "Next Due").to.equal(in_days(win, 365));
				expect(doc.completed_by, "Completed By").to.equal(users[MAINTENANCE]);
				expect(doc.impact_status, "Impact Assessment").to.equal("Not needed");
				made.calibration = doc.name;
			});
			signed_as("Complete", "Verified");

			cy.then(() => open_report("Equipment History Card", { asset: made.asset })).then(
				(lines) => {
					const line = lines.find((each) => each.record === made.calibration);
					expect(line, "the calibration on the history card").to.be.ok;
					expect(line.event).to.equal("Calibration: Pass");
					expect(line.record_type).to.equal("Calibration Record");
					expect(line.by, "signed by").to.equal(users[MAINTENANCE]);
				}
			);
			no_problems("D10-T06");
		});
	});

	it("[URS-EQP-002] D10-T07 An incomplete calibration is refused", () => {
		prepare("prepare_instrument", { password, stage: "out_of_tolerance" }).then((made) => {
			sign_in(MAINTENANCE);
			calibration_form({ asset: made.asset, by: "In-house" });
			save_refused(
				refusal("An in-house calibration follows an effective SOP or work instruction.")
			);
			fill("calibrated_by", "External laboratory", "Select");
			fill("laboratory", data.laboratory, "Link");
			save_refused(
				refusal(
					"An external calibration names the laboratory, its certificate number and the certificate."
				)
			);
			cy.window().then((win) => fill("performed_on", in_days(win, 1), "Date"));
			save_refused(refusal("A calibration is recorded once it is done."));
			cy.window().should((win) => expect(win.cur_frm.is_new(), "nothing saved").to.be.ok);
			no_problems("D10-T07, the refusals");

			// A draft calibration: no Delete in its menu, and refused by any path.
			open_form("Calibration Record", made.draft);
			form_menu_offers("Delete", false);
			refused_call("frappe.client.delete", {
				doctype: "Calibration Record",
				name: made.draft,
			});
			as_administrator();
			refused_call("frappe.client.delete", {
				doctype: "Calibration Record",
				name: made.draft,
			}).should("match", exact("A calibration record is never deleted."));
			stored_values("Calibration Record", made.draft, ["docstatus"]).then((values) =>
				expect(values?.docstatus, "still there, a draft").to.equal(0)
			);
			no_problems("D10-T07");
		});
	});

	it("[URS-EQP-003] D10-T08 A value found out of tolerance makes QA assess the impact: no product impact", () => {
		prepare("prepare_measured_instrument", { password }).then((made) => {
			const was_at = made.workstations["Was At"];
			const now_at = made.workstations["Now At"];
			sign_in(MAINTENANCE);
			open_form("Calibration Record", made.draft);
			sign("Complete", { meaning: "Verified" });
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "completed").to.equal(1);
				expect(doc.result, "Result").to.equal("Pass");
				expect(doc.out_of_tolerance, "Found Out of Tolerance").to.equal(1);
				expect(doc.impact_status, "Impact Assessment").to.equal("Open");
				expect(doc.last_good_calibration, "Since").to.equal(TODAY);
				// Since the sheet: the job cards of each workstation it was at, each naming it,
				// read from the audit trail; none of a workstation it never was at.
				const listed = Object.fromEntries(
					rows(win, "impact").map((row) => [
						row.reference_name,
						[row.reference_doctype, row.workstation || null],
					])
				);
				expect(listed, "Records").to.deep.equal({
					[made.inspection]: ["Quality Inspection", null],
					[made.job_cards["Was At"]]: ["Job Card", was_at],
					[made.job_cards["Now At"]]: ["Job Card", now_at],
				});
			});
			cy.contains(
				`${FORM} .frappe-control[data-fieldname="impact"]`,
				made.job_cards["Was At"]
			).should("be.visible");
			// Since the sheet: the completion's signature binds the rows it listed, and verifies.
			signatures_bind("Calibration Record", made.draft, ["Complete"]);
			prepare("run_impact_reminders");

			sign_in(QA);
			open_list("Notification Log", { document_name: made.draft });
			cy.window().should((win) =>
				expect(win.cur_list.data, "QA's notifications").to.have.length.at.least(1)
			);
			cy.call("frappe.client.get_list", {
				doctype: "Notification Log",
				filters: {
					document_type: "Calibration Record",
					document_name: made.draft,
					for_user: users[QA],
				},
				fields: ["subject"],
			}).then(({ message }) =>
				cy
					.window()
					.then((win) =>
						expect(message.map((row) => plain(row.subject))).to.include(
							`Out of tolerance: ${made.asset} on ${user_date(win, TODAY)}`
						)
					)
			);
			open_list("Reminder", { reference_name: made.draft });
			cy.call("frappe.client.get_list", {
				doctype: "Reminder",
				filters: { category: "Calibration Impact", reference_name: made.draft },
				fields: ["subject", "status"],
			}).then(({ message }) =>
				expect(message.map((row) => [row.subject, row.status])).to.deep.equal([
					[
						`Assess what ${made.asset} measured before it was found out of tolerance (${made.draft})`,
						"Open",
					],
				])
			);

			const reason =
				"Readings since last calibration re-checked on retained samples; all within specification";
			open_form("Calibration Record", made.draft);
			sign("Assess: No Product Impact", {
				group: "Assess Impact",
				meaning: "Reviewed",
				reason,
			});
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.impact_status, "Impact Assessment").to.equal("Assessed");
				expect(doc.impact_decision, "Decision").to.equal("No product impact");
				expect(doc.assessed_by, "Assessed By").to.equal(users[QA]);
				expect(String(doc.assessed_on).slice(0, 10), "Assessed On").to.equal(TODAY);
			});
			signed_as("Assess: No Product Impact", "Reviewed");
			group_offered("Assess Impact", false);
			integrity_verified();
			signatures_bind("Calibration Record", made.draft, [
				"Complete",
				"Assess: No Product Impact",
			]);

			// The next daily run completes the reminder.
			prepare("run_impact_reminders");
			sign_in(QA);
			cy.call("frappe.client.get_list", {
				doctype: "Reminder",
				filters: { category: "Calibration Impact", reference_name: made.draft },
				fields: ["status"],
			}).then(({ message }) =>
				expect(message.map((row) => row.status)).to.deep.equal(["Completed"])
			);
			no_problems("D10-T08");
		});
	});

	it("[URS-EQP-003] D10-T09 QA assesses an out-of-tolerance finding as needing product action; others cannot assess", () => {
		prepare("prepare_instrument", { password, stage: "out_of_tolerance" }).then((made) => {
			sign_in(MAINTENANCE);
			open_form("Calibration Record", made.draft);
			sign("Complete");
			cy.window().should((win) => {
				expect(current(win).docstatus, "completed").to.equal(1);
				expect(current(win).impact_status, "Impact Assessment").to.equal("Open");
			});
			group_offered("Assess Impact", false);
			refused_signing(
				"Calibration Record",
				made.draft,
				"Assess: Product Action Needed"
			).should("match", exact("Only QA assesses the impact."));

			sign_in(QA);
			open_form("Calibration Record", made.draft);
			group_offered("Assess Impact", true);
			sign("Assess: Product Action Needed", {
				group: "Assess Impact",
				meaning: "Reviewed",
				reason: "Lots measured since 15-09 to be re-inspected",
			});
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.impact_status, "Impact Assessment").to.equal("Assessed");
				expect(doc.impact_decision, "Decision").to.equal("Product action needed");
				expect(doc.assessed_by, "Assessed By").to.equal(users[QA]);
			});
			signed_as("Assess: Product Action Needed", "Reviewed");
			group_offered("Assess Impact", false);
			no_problems("D10-T09");
		});
	});

	it("[URS-EQP-001] [URS-EQP-005] D10-T10 QA corrects a completed calibration; the instrument is not fit until the amendment is completed", () => {
		const made = {};
		prepare("prepare_instrument", { password, stage: "calibrated" }).then((caliper) => {
			made.caliper = caliper;
		});
		cy.then(() => prepare("prepare_inspection", { password })).then((inspection) => {
			made.inspection = inspection.inspection;
		});
		cy.then(() => {
			const asset = made.caliper.asset;
			const calibration = made.caliper.calibration;
			sign_in(QA);
			stored("Calibration Record", calibration).then((doc) => {
				made.original = doc;
			});
			open_form("Calibration Record", calibration);
			offered("Cancel", false);

			// The reason is required: the dialog does not sign without one.
			open_signing("Correct");
			statement_is("Rejected");
			enter_password();
			press_sign();
			refused_with(/Missing Values Required: .*Reason/);
			enter_reason("Certificate number typed wrongly");
			enter_password();
			press_sign();
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			form_is(
				"Calibration Record",
				(doc) => doc.amended_from === calibration && doc.docstatus === 0
			);
			cy.window().should((win) => {
				const doc = current(win);
				const original = made.original;
				for (const field of [
					"asset",
					"performed_on",
					"calibrated_by",
					"laboratory",
					"certificate_number",
					"certificate",
				]) {
					expect(doc[field], field).to.equal(original[field]);
				}
				expect(
					doc.points.map((p) => [p.parameter, p.as_found, p.as_left]),
					"the points"
				).to.deep.equal(original.points.map((p) => [p.parameter, p.as_found, p.as_left]));
				made.amendment = doc.name;
			});
			stored("Calibration Record", calibration).then((doc) =>
				expect(doc.docstatus, "the corrected record is cancelled").to.equal(2)
			);
			cy.then(() => open_form("Calibration Record", calibration));
			signed_as("Correct", "Rejected");

			not_fit_because(asset).should(
				"contain",
				`its calibration ${calibration} is being corrected`
			);
			no_problems("D10-T10, the correction");

			// An inspection reading naming it is refused with that reason.
			sign_in(QC);
			open_form("Quality Inspection", made.inspection);
			fill_row("readings", 2, "mds_instrument", asset, "Link");
			save_refused(
				refusal(
					`Instrument ${asset} is not used for an inspection: its calibration ${calibration} is being corrected`,
					"Equipment not usable"
				)
			);
			no_problems("D10-T10, the inspection");

			// Maintenance completes the amendment.
			sign_in(MAINTENANCE);
			cy.then(() => open_form("Calibration Record", made.amendment));
			fill("certificate_number", "DCL-TEST-01-CORRECTED");
			save();
			sign("Complete", { meaning: "Verified" });
			cy.window().should((win) => {
				expect(current(win).docstatus, "completed").to.equal(1);
				expect(current(win).certificate_number).to.equal("DCL-TEST-01-CORRECTED");
			});
			offered("Correct", false);
			cy.then(() =>
				refused_signing("Calibration Record", made.amendment, "Correct").should(
					"match",
					exact("Only QA corrects a completed record.")
				)
			);
			not_fit_because(asset).should("equal", "");
			no_problems("D10-T10");
		});
	});

	it("[URS-MST-010] D10-T11 QA records a production machine that needs maintenance and qualification", () => {
		prepare("unique_tag").then((tag) => {
			sign_in(QA);
			asset_form({
				item: data.machine_item,
				name: `Test Tipping Machine ${tag}`,
				equipment: [
					["mds_equipment_tag", `TM-TEST-01-${tag}`],
					["mds_equipment_class", "Production machine", "Select"],
					["mds_manufacturer", "Demo Machines"],
					["mds_model", "TM-02"],
					["mds_serial_number", `TM-SN-${tag}`],
					["mds_affects_quality", 1, "Check"],
					["mds_needs_maintenance", 1, "Check"],
					["mds_maintenance_interval_days", 30],
					["mds_needs_qualification", 1, "Check"],
					["mds_requalification_interval_days", 365],
				],
			});
			cy.window().should((win) =>
				expect(current(win).mds_workstation || null, "Workstation left empty").to.equal(
					null
				)
			);
			save();
			banner(
				"orange",
				"Not yet approved by QA, so no inspection uses it. Ready for QA's approval."
			);
			sign("Approve Equipment", {
				act: "Approve",
				meaning: "Approved",
				reason: "Installed in moulding hall",
			});
			banner(
				"green",
				"Equipment approved by QA; what was approved changes under change control."
			);
			cy.window()
				.then((win) => care_of(current(win).name))
				.then((line) => {
					expect(line.calibration, "Calibration").to.equal("Not needed");
					expect(line.maintenance, "Maintenance").to.equal("Never done");
					expect(line.qualification, "Qualification").to.equal("Never done");
					expect(line.not_fit, "Not Fit Because").to.contain(
						"it has had no preventive maintenance"
					);
					expect(line.not_fit, "Not Fit Because").to.contain(
						"it is not qualified (IQ, OQ and PQ)"
					);
				});
			no_problems("D10-T11");
		});
	});

	it("[URS-EQP-004] D10-T12 Maintenance completes preventive maintenance; a Not OK check is not satisfactory", () => {
		prepare("prepare_machine", { password, needs: "maintenance" }).then((machine) => {
			const made = {};
			sign_in(MAINTENANCE);
			maintenance_form({
				asset: machine.asset,
				checks: [
					["Lubrication", "OK"],
					["Belt tension", "Not OK"],
				],
				actions: "Greased rails; belt worn",
				downtime: 1,
			});
			save();
			cy.window().should((win) =>
				expect(current(win).result, "Result").to.equal("Not satisfactory")
			);
			sign("Complete", { meaning: "Verified" });
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "completed").to.equal(1);
				expect(doc.next_due_date || null, "Next Due").to.equal(null);
				made.first = doc.name;
			});
			cy.window().then((win) =>
				not_fit_because(machine.asset).should(
					"contain",
					`its last preventive maintenance on ${user_date(win, TODAY)} did not pass`
				)
			);

			maintenance_form({
				asset: machine.asset,
				checks: [
					["Lubrication", "OK"],
					["Belt tension", "OK"],
				],
				actions: "Belt replaced",
				downtime: 1,
			});
			save();
			cy.window().should((win) =>
				expect(current(win).result, "Result").to.equal("Satisfactory")
			);
			sign("Complete");
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "completed").to.equal(1);
				expect(doc.interval_days, "Interval (days)").to.equal(30);
				expect(doc.next_due_date, "Next Due").to.equal(in_days(win, 30));
			});
			not_fit_because(machine.asset).should("equal", "");

			maintenance_form({
				asset: machine.asset,
				checks: [["Lubrication", "OK"]],
				actions: "Greased rails",
				downtime: -2,
			});
			save_refused(refusal("Downtime is a number of hours."));
			no_problems("D10-T12, the records");

			// A completed record: no Cancel, nothing to save, and no change by any path.
			cy.then(() => open_form("Maintenance Record", made.first));
			offered("Cancel", false);
			nothing_to_save();
			cy.then(() =>
				refused_call("frappe.client.set_value", {
					doctype: "Maintenance Record",
					name: made.first,
					fieldname: "actions_performed",
					value: "Changed afterwards",
				})
			);
			cy.then(() =>
				stored_values("Maintenance Record", made.first, ["actions_performed"])
			).then((values) =>
				expect(values.actions_performed, "unchanged").to.equal("Greased rails; belt worn")
			);
			no_problems("D10-T12");
		});
	});

	it("[URS-EQP-004] D10-T13 QA corrects a completed maintenance record", () => {
		prepare("prepare_machine", { password, needs: "maintenance", stage: "maintained" }).then(
			(machine) => {
				const made = {};
				sign_in(QA);
				open_form("Maintenance Record", machine.maintenance);
				offered("Cancel", false);
				sign("Correct", { meaning: "Rejected", reason: "Wrong date entered" });
				form_is(
					"Maintenance Record",
					(doc) => doc.amended_from === machine.maintenance && doc.docstatus === 0
				);
				cy.window().then((win) => {
					made.amendment = current(win).name;
				});
				stored("Maintenance Record", machine.maintenance).then((doc) =>
					expect(doc.docstatus, "the corrected record is cancelled").to.equal(2)
				);
				open_form("Maintenance Record", machine.maintenance);
				signed_as("Correct", "Rejected");
				not_fit_because(machine.asset).should(
					"contain",
					`its preventive maintenance ${machine.maintenance} is being corrected`
				);

				sign_in(MAINTENANCE);
				cy.then(() => open_form("Maintenance Record", made.amendment));
				sign("Complete");
				cy.window().should((win) => {
					expect(current(win).docstatus, "completed").to.equal(1);
					expect(current(win).result).to.equal("Satisfactory");
				});
				not_fit_because(machine.asset).should("equal", "");
				no_problems("D10-T13");
			}
		);
	});

	it("[URS-EQP-011] D10-T14 A qualification out of order is refused", () => {
		prepare("prepare_machine", { password, needs: "qualification" }).then((machine) => {
			sign_in(MAINTENANCE);
			qualification_form({
				asset: machine.asset,
				stage: "OQ",
				protocol: data.protocol,
				tests: [PASSED_TEST],
			});
			save_refused(refusal("An OQ or PQ follows an approved, passed IQ."));
			fill("stage", "PQ", "Select");
			save_refused(refusal("An OQ or PQ follows an approved, passed IQ."));
			fill("stage", "IQ", "Select");
			fill("protocol", data.work_instruction, "Link");
			save_refused(refusal("The protocol is an effective SOP or form."));
			cy.window().should((win) => expect(win.cur_frm.is_new(), "nothing saved").to.be.ok);
			no_problems("D10-T14");
		});
	});

	it("[URS-EQP-010] D10-T15 IQ executed by Maintenance, returned by QA, executed again and approved", () => {
		prepare("prepare_machine", { password, needs: "qualification" }).then((machine) => {
			const made = {};
			sign_in(MAINTENANCE);
			qualification_form({
				asset: machine.asset,
				stage: "IQ",
				protocol: data.protocol,
				tests: [
					{
						test: "Power supply",
						criteria: "230 V AC +/- 10 %, earthed",
						observed: "Matches the specification",
						outcome: "Pass",
					},
					{
						test: "Guards fitted",
						criteria: "All guards in place and interlocked",
						observed: "All fitted and interlocked",
						outcome: "Pass",
					},
				],
			});
			save();
			sign("Execute", { meaning: "Verified" });
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "still a draft").to.equal(0);
				expect(doc.executed_by, "Executed By").to.equal(users[MAINTENANCE]);
				expect(doc.executed_at, "Executed At").to.be.ok;
				made.iq = doc.name;
			});
			offered("Approve", false);
			offered("Return", false);
			cy.then(() =>
				refused_signing("Equipment Qualification", made.iq, "Approve").should(
					"match",
					exact("Only QA approves a qualification.")
				)
			);
			no_problems("D10-T15, executed");

			sign_in(QA);
			cy.then(() => open_form("Equipment Qualification", made.iq));
			sign("Return", { meaning: "Rejected", reason: "Attach the electrical test values" });
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "still a draft").to.equal(0);
				expect(doc.executed_by || null, "Executed By").to.equal(null);
				expect(doc.executed_at || null, "Executed At").to.equal(null);
			});

			sign_in(MAINTENANCE);
			cy.then(() => open_form("Equipment Qualification", made.iq));
			offered("Execute", true);
			open_line("tests", 1);
			fill_line("observed", "231 V AC; earth continuity 0.05 ohm; insulation 200 Mohm");
			close_line();
			save();
			sign("Execute", { meaning: "Verified" });

			sign_in(QA);
			cy.then(() => open_form("Equipment Qualification", made.iq));
			sign("Approve", { meaning: "Approved", reason: "IQ complete" });
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "completed").to.equal(1);
				expect(doc.outcome, "Outcome").to.equal("Pass");
				expect(doc.approved_by, "Approved By").to.equal(users[QA]);
				expect(
					(doc.__onload?.signatures || []).map((s) => [s.meaning, s.action]),
					"electronic signatures"
				).to.deep.equal([
					["Verified", "Execute"],
					["Rejected", "Return"],
					["Verified", "Execute"],
					["Approved", "Approve"],
				]);
			});

			// An OQ can now be saved for this machine.
			sign_in(MAINTENANCE);
			qualification_form({
				asset: machine.asset,
				stage: "OQ",
				protocol: data.protocol,
				tests: [PASSED_TEST],
			});
			save();
			cy.window().should((win) => {
				expect(current(win).stage).to.equal("OQ");
				expect(current(win).docstatus).to.equal(0);
			});
			no_problems("D10-T15");
		});
	});

	it("[URS-EQP-012] [URS-EQP-013] D10-T16 QA corrects an approved qualification", () => {
		prepare("prepare_machine", {
			password,
			needs: "qualification",
			stage: "iq_approved",
		}).then((machine) => {
			const made = {};
			sign_in(QA);
			open_form("Equipment Qualification", machine.qualification);
			offered("Cancel", false);
			sign("Correct", {
				meaning: "Rejected",
				reason: "Wrong protocol revision referenced",
			});
			form_is(
				"Equipment Qualification",
				(doc) => doc.amended_from === machine.qualification && doc.docstatus === 0
			);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.executed_by || null, "Executed By").to.equal(null);
				expect(doc.approved_by || null, "Approved By").to.equal(null);
				made.amendment = doc.name;
			});
			stored("Equipment Qualification", machine.qualification).then((doc) =>
				expect(doc.docstatus, "the corrected IQ is cancelled").to.equal(2)
			);
			open_form("Equipment Qualification", machine.qualification);
			signed_as("Correct", "Rejected");
			not_fit_because(machine.asset).should(
				"contain",
				`its qualification ${machine.qualification} is being corrected`
			);

			// Until the amendment is executed and approved.
			sign_in(MAINTENANCE);
			cy.then(() => open_form("Equipment Qualification", made.amendment));
			sign("Execute");
			sign_in(QA);
			cy.then(() => open_form("Equipment Qualification", made.amendment));
			sign("Approve", { reason: "IQ complete, protocol revision corrected" });
			cy.window().should((win) => expect(current(win).docstatus).to.equal(1));
			not_fit_because(machine.asset).then((why) => {
				expect(why).to.not.contain("is being corrected");
				expect(why).to.contain("it is not qualified (IQ, OQ and PQ)");
			});
			no_problems("D10-T16, the correction");
		});

		// Since the sheet: a qualified machine moved to another workstation on its record, under an
		// approved change request, is qualified again from the IQ.
		prepare("prepare_machine", {
			password,
			needs: "qualification",
			stage: "qualified",
			at_workstation: 1,
		}).then((machine) => {
			prepare("prepare_change", { password, asset: machine.asset }).then((change) => {
				sign_in(QA);
				not_fit_because(machine.asset).should("equal", "");
				open_form("Asset", machine.asset);
				fill("mds_workstation", machine.workstations["Line B"], "Link");
				fill("mds_change_request", change, "Link");
				save();
				cy.window().should((win) => {
					expect(current(win).mds_workstation).to.equal(machine.workstations["Line B"]);
					expect(current(win).mds_changed_under, "Last Changed Under").to.equal(change);
					expect(current(win).mds_version, "Version").to.equal(machine.version + 1);
				});
				cy.window().then((win) =>
					not_fit_because(machine.asset).should(
						"equal",
						`it was moved on ${user_date(
							win,
							TODAY
						)} and has not been qualified again since (IQ, OQ, PQ)`
					)
				);
			});
			no_problems("D10-T16");
		});
	});

	it("[URS-EQP-009] D10-T17 ERPNext's own Asset Maintenance cannot be used", () => {
		prepare("prepare_instrument", { password, stage: "approved" }).then((made) => {
			sign_in(QA);
			new_form("Asset Maintenance");
			cy.window().then((win) => {
				if (!current(win).company) fill("company", data.company, "Link");
			});
			// Its lookup lists submitted assets only (ERPNext's accounting); the person types the
			// equipment's name as read off its record.
			fill("asset_name", made.asset);
			fill("maintenance_team", data.maintenance_team, "Link");
			rows_up_to("asset_maintenance_tasks", 1);
			open_line("asset_maintenance_tasks", 1);
			fill_line("maintenance_task", "Monthly lubrication");
			fill_line("maintenance_status", "Planned", "Select");
			fill_line("periodicity", "Monthly", "Select");
			close_line();
			save_refused(
				refusal(
					"Calibration and maintenance are recorded as Calibration and Maintenance Records, signed.",
					"Not used"
				)
			);
			cy.window().should((win) => expect(win.cur_frm.is_new(), "nothing saved").to.be.ok);
			no_problems("D10-T17");
		});
	});

	it("[URS-MST-011] [URS-EQP-007] D10-T18 Management reads the history card and the care schedule", () => {
		prepare("prepare_measured_instrument", { password, with_draft: 0 }).then((made) => {
			const was_at = made.workstations["Was At"];
			const now_at = made.workstations["Now At"];
			sign_in(MANAGEMENT);
			open_report("Equipment History Card", { asset: made.asset }).then((lines) => {
				const events = lines.map((line) => line.event);
				expect(events, "when it was recorded").to.include("Recorded");
				const approval = lines.find((line) => line.event === "Signed: Approved");
				expect(approval?.by, "QA's approval signature").to.equal(users[QA]);
				const moved = lines.find((line) =>
					String(line.details).includes(`Workstation: ${was_at} to ${now_at}`)
				);
				expect(moved, "the change, with the request it was made under").to.be.ok;
				expect(moved.details).to.contain(made.change_request);
				const calibration = lines.find((line) => line.record === made.calibration);
				expect(calibration?.event, "its signed calibration").to.equal("Calibration: Pass");
				expect(calibration?.by).to.equal(users[MAINTENANCE]);
				const inspection = lines.find((line) => line.record === made.inspection);
				expect(inspection?.event, "the inspection that used it").to.equal(
					"Used on inspection (Accepted)"
				);
				const dates = lines.map((line) => String(line.date));
				expect(dates, "newest first").to.deep.equal([...dates].sort().reverse());
			});
			report_summary().then((summary) => {
				expect(summary, "the summary").to.include({
					Repairs: "0",
					"Found out of tolerance": "0",
					"Inspections using it": "1",
				});
				expect(Number.parseFloat(summary["Downtime (hours)"]), "downtime").to.equal(0);
			});

			care_of(made.asset).then((line) =>
				cy.window().then((win) => {
					expect(line.workstation, "Workstation").to.equal(now_at);
					expect(line.calibration_due, "Calibration Due").to.equal(in_days(win, 365));
					expect(line.calibration, "Calibration").to.equal("In date");
					expect(line.maintenance, "Maintenance").to.equal("Not needed");
					expect(line.qualification, "Qualification").to.equal("Not needed");
					expect(line.not_fit || "", "Not Fit Because").to.equal("");
				})
			);

			open_form("Asset", made.asset);
			cy.window().should((win) =>
				expect(win.cur_frm.perm[0].write || 0, "Management may not change it").to.equal(0)
			);
			nothing_to_save();
			offered("Approve Equipment", false);
			banner(
				"green",
				"Equipment approved by QA; what was approved changes under change control."
			);
			no_problems("D10-T18");
		});
	});
});
